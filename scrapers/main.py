"""
Scrapers service entry point.

Starts a BackgroundScheduler and keeps the process alive with a heartbeat loop.
The heartbeat file (/tmp/scheduler_health) is touched every 60 s so the Docker
health check can verify the scheduler is running.
"""
import atexit
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from signals.google_places import scrape_place
from signals.foursquare import scrape_venue
from signals.health_inspections import scrape_inspections
from signals.outscraper_reviews import scrape_outscraper_reviews
from signals.outscraper_quota import monthly_summary as outscraper_quota_summary, log_run_status
from signals.tabc_license import scrape_license
from signals.hours_monitor import scrape_hours
from signals.sba_loans import scrape_sba_loans
from signals.property_tax import scrape_property_tax
from signals.delivery_platforms import scrape_delivery_platforms
from scoring.engine_v2 import compute_scores_v2
from backtesting.outcome_tracker import run_outcome_tracker

# load_dotenv is a no-op inside Docker (env vars already injected by docker-compose).
# It only activates when running locally with a .env file.
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# The health check reads this file's mtime to verify the scheduler is alive.
HEARTBEAT_FILE = Path("/tmp/scheduler_health")
HEARTBEAT_INTERVAL = 60  # seconds between heartbeat updates

# Per-restaurant config that isn't stored in the DB yet (Foursquare lat/lng).
# Keyed by google_place_id so the scrape jobs can look it up after querying restaurants.
# In a future phase these coords would live in the restaurants table.
_RESTAURANT_CONFIG: dict[str, dict] = {
    "ChIJGXYxd92YToYR7yV_BSMQ2Xk": {   # Pecan Lodge, Deep Ellum, Dallas TX
        "foursquare_lat": 32.7828,
        "foursquare_lng": -96.7834,
    },
}


def _build_engine():
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB", "bizhealth")
    user = os.environ.get("POSTGRES_USER", "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    # create_engine returns a connection-pooled engine — safe to share across threads.
    # Unlike EF Core's DbContext (which is not thread-safe), the engine itself is.
    # Each job gets its own Session (equivalent to a scoped DbContext lifetime in C#).
    return create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)


# Module-level engine — created once, shared across job threads via connection pool.
engine = _build_engine()


def _get_restaurants(session: Session):
    return session.execute(
        text(
            "SELECT id, name, city, state, zip, google_place_id "
            "FROM restaurants WHERE google_place_id IS NOT NULL"
        )
    ).fetchall()


def run_daily_scrape() -> None:
    """Google Places + Foursquare + hours monitor + scoring for every tracked restaurant.

    This is a APScheduler job function — the scheduler calls it in a background thread.
    In C# this would be the ExecuteAsync() body of a BackgroundService, or a Hangfire
    job method decorated with [AutomaticRetry]. APScheduler does NOT retry on failure
    by default; wrap in try/except to prevent a crash from killing the scheduler.
    """
    logger.info("[daily] job started")
    with Session(engine) as session:
        for r in _get_restaurants(session):
            rid    = str(r.id)
            config = _RESTAURANT_CONFIG.get(r.google_place_id, {})

            for label, fn, args in [
                ("google_places",    scrape_place,   (r.google_place_id, rid, session)),
                ("foursquare",       _scrape_fsq,    (r, rid, config, session)),
                ("hours_monitor",    scrape_hours,   (rid, session)),
                ("scoring_v2",       _score,         (r.name, rid, session)),
            ]:
                try:
                    fn(*args)
                    logger.info("[daily] %s — %s OK", r.name, label)
                except Exception as exc:
                    # Log and continue — one failed scraper must not stop the others.
                    # In C# you'd catch Exception in ExecuteAsync() and log via ILogger.
                    logger.error("[daily] %s — %s FAILED: %s", r.name, label, exc)

    logger.info("[daily] job complete")


def run_sba_loans_scrape() -> None:
    """Fetch SBA loan history for all tracked restaurants.

    Runs weekly on Sunday at 1:30 AM UTC — after the Outscraper job (1:00 AM)
    and before the daily scoring run (5:00 AM).
    SBA Data.gov is a free public dataset with no API key required.
    """
    logger.info("[sba_loans] job started")
    with Session(engine) as session:
        for r in _get_restaurants(session):
            rid      = str(r.id)
            zip_code = r.zip or ""
            if not zip_code:
                logger.warning("[sba_loans] %s — no zip code, skipping", r.name)
                continue
            try:
                scrape_sba_loans(r.name, zip_code, rid, session)
                logger.info("[sba_loans] %s — OK", r.name)
            except Exception as exc:
                logger.error("[sba_loans] %s — FAILED: %s", r.name, exc)
    logger.info("[sba_loans] job complete")


def run_property_tax_scrape() -> None:
    """Fetch business personal property tax status for all tracked restaurants.

    Runs weekly on Sunday at 2:00 AM UTC — after SBA loans (1:30 AM)
    and before the daily scoring run (5:00 AM).
    Most DFW CADs do not expose public APIs; this job records no_data_available
    gracefully when that is the case.
    """
    logger.info("[property_tax] job started")
    with Session(engine) as session:
        for r in _get_restaurants(session):
            rid = str(r.id)
            try:
                scrape_property_tax(r.name, r.city or "", rid, session)
                logger.info("[property_tax] %s — OK", r.name)
            except Exception as exc:
                logger.error("[property_tax] %s — FAILED: %s", r.name, exc)
    logger.info("[property_tax] job complete")


def run_delivery_platforms_scrape() -> None:
    """Check DoorDash and Uber Eats listing status for all tracked restaurants.

    Runs weekly on Monday at 5:00 AM UTC — after health inspections and TABC
    (Monday 4:00-4:30 AM UTC) so all operational signals are fresh before scoring.
    No API key required — uses public consumer-facing search endpoints.
    platform_unavailable results are logged but do not trigger scoring adjustments.
    """
    logger.info("[delivery_platforms] job started")
    with Session(engine) as session:
        for r in _get_restaurants(session):
            rid = str(r.id)
            try:
                scrape_delivery_platforms(
                    r.name, r.address or "", r.city or "", rid, session
                )
                logger.info("[delivery_platforms] %s — OK", r.name)
            except Exception as exc:
                logger.error("[delivery_platforms] %s — FAILED: %s", r.name, exc)
    logger.info("[delivery_platforms] job complete")


_BACKFILL_CUTOFF  = datetime(2026, 5, 25, tzinfo=timezone.utc)
_BACKFILL_REVIEWS = 200


def run_outscraper_backfill() -> None:
    """One-time startup backfill: fetch 200 reviews for any restaurant whose earliest
    outscraper_reviews signal postdates May 25, 2026.

    Backfill records are tracked separately and do not count against the monthly cap.
    After this job completes the regular biweekly schedule takes over at 70 reviews/run.
    """
    logger.info("[outscraper_backfill] checking for restaurants that need May 25 history")

    needs_backfill = []
    with Session(engine) as session:
        for r in _get_restaurants(session):
            rid      = str(r.id)
            earliest = session.execute(
                text(
                    "SELECT MIN(scraped_at) FROM raw_signals "
                    "WHERE restaurant_id = CAST(:rid AS uuid) AND source = 'outscraper_reviews'"
                ),
                {"rid": rid},
            ).scalar()
            if earliest is None or earliest.replace(tzinfo=timezone.utc) > _BACKFILL_CUTOFF:
                needs_backfill.append(r)

    if not needs_backfill:
        logger.info("[outscraper_backfill] all restaurants already have history from May 25 — skipping")
        return

    logger.info(
        "[outscraper_backfill] %d restaurants need backfill — fetching %d reviews each",
        len(needs_backfill), _BACKFILL_REVIEWS,
    )

    completed = 0
    with Session(engine) as session:
        for r in needs_backfill:
            rid = str(r.id)
            try:
                scrape_outscraper_reviews(
                    r.google_place_id, r.name, rid, session,
                    city=r.city, max_reviews=_BACKFILL_REVIEWS, is_backfill=True,
                )
                completed += 1
                logger.info("[outscraper_backfill] %s — OK", r.name)
            except Exception as exc:
                logger.error("[outscraper_backfill] %s — FAILED: %s", r.name, exc)

    logger.info(
        "[outscraper_backfill] complete — %d/%d restaurants now have history from May 25 2026",
        completed, len(needs_backfill),
    )


def run_outscraper_scrape() -> None:
    """Fetch Outscraper review history for all tracked restaurants.

    Runs biweekly (1st and 15th at 1:00 AM UTC) — 70 reviews/restaurant per run,
    107 restaurants × 70 × 2 = 14,980 records/month (~$44.94).
    """
    logger.info(
        "[outscraper] job started — biweekly schedule (1st and 15th), "
        "70 reviews/restaurant, ~14,980 records/month"
    )
    with Session(engine) as session:
        summary = outscraper_quota_summary(session)
        records_before = summary["records_used"]
        logger.info(
            "[outscraper] quota: %d/%d records used (%s) — %d remaining",
            summary["records_used"], summary["cap"],
            summary["estimated_cost"], summary["records_remaining"],
        )
        if summary["records_remaining"] == 0:
            logger.warning("[outscraper] monthly quota exhausted — skipping entire batch")
            log_run_status("skipped", 0, 0, records_before, records_before, session)
            return

        completed = 0
        quota_hit = 0
        for r in _get_restaurants(session):
            rid = str(r.id)
            try:
                result = scrape_outscraper_reviews(r.google_place_id, r.name, rid, session, city=r.city)
                if result.get("quota_exceeded"):
                    quota_hit += 1
                    logger.warning("[outscraper] %s — quota exhausted mid-run", r.name)
                else:
                    completed += 1
                    logger.info("[outscraper] %s — OK", r.name)
            except Exception as exc:
                logger.error("[outscraper] %s — FAILED: %s", r.name, exc)

        fresh = outscraper_quota_summary(session)
        status = "throttled" if quota_hit > 0 else "ok"
        log_run_status(status, completed, quota_hit, records_before, fresh["records_used"], session)

    logger.info("[outscraper] job complete — status=%s completed=%d quota_hit=%d", status, completed, quota_hit)


def run_weekly_scrape() -> None:
    """Health inspections + TABC license check + re-score for every tracked restaurant."""
    logger.info("[weekly] job started")
    with Session(engine) as session:
        for r in _get_restaurants(session):
            rid = str(r.id)

            for label, fn, args in [
                ("health_inspections", scrape_inspections, (r.name, r.city, rid, session)),
                ("tabc_license",       scrape_license,     (r.name, r.city, rid, session)),
                ("scoring_v2",         _score,             (r.name, rid, session)),
            ]:
                try:
                    fn(*args)
                    logger.info("[weekly] %s — %s OK", r.name, label)
                except Exception as exc:
                    logger.error("[weekly] %s — %s FAILED: %s", r.name, label, exc)

    logger.info("[weekly] job complete")


# ── private helpers ────────────────────────────────────────────────────────────

def _scrape_fsq(r, rid: str, config: dict, session: Session) -> None:
    """Wrap scrape_venue so it's skippable when coords are missing."""
    lat = config.get("foursquare_lat")
    lng = config.get("foursquare_lng")
    if lat is None or lng is None:
        logger.warning("Foursquare skipped for %s — no coords in _RESTAURANT_CONFIG", r.name)
        return
    scrape_venue(r.name, lat, lng, rid, session)


def _score(name: str, rid: str, session: Session) -> None:
    scores = compute_scores_v2(rid, session)
    logger.info("Scored %s — overall=%s", name, scores["overall_score"])


def run_backtest_outcome_tracker() -> None:
    """Check 90/180-day outcomes for prospective backtest cohort restaurants.

    Runs weekly on Sunday at 3:00 AM UTC. Checks Google Places status, TABC,
    review recency gaps, and hours changes to determine current operational status.
    """
    logger.info("[backtest_outcome] job started")
    with Session(engine) as session:
        try:
            summary = run_outcome_tracker(session)
            logger.info("[backtest_outcome] complete: %s", summary)
        except Exception as exc:
            logger.error("[backtest_outcome] FAILED: %s", exc)
    logger.info("[backtest_outcome] job complete")


# ── scheduler setup ────────────────────────────────────────────────────────────

def main() -> None:
    # BackgroundScheduler runs jobs in daemon threads alongside the main thread.
    # This is different from C#'s IHostedService model where the framework owns the
    # process lifetime — here we must keep the main thread alive ourselves (the loop
    # below). If the main thread exits, daemon threads are killed immediately.
    #
    # The alternative, BlockingScheduler, parks the main thread inside the scheduler
    # itself, which would prevent us from running the heartbeat loop.
    scheduler = BackgroundScheduler(timezone="UTC")

    # IntervalTrigger fires every N hours/weeks from the moment the scheduler starts.
    # Equivalent to Hangfire's RecurringJob.AddOrUpdate with a period, or a C# Timer.
    #
    # coalesce=True: if the container was down and missed scheduled runs, fire once
    # immediately on restart rather than catching up each missed execution.
    # In Quartz.NET this maps to WithMisfireHandlingInstructionFireNow().
    #
    # max_instances=1: never run two copies of the same job concurrently.
    # Equivalent to [DisallowConcurrentExecution] in Quartz.NET.
    scheduler.add_job(
        run_daily_scrape,
        IntervalTrigger(hours=24),
        id="daily_scrape",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        run_weekly_scrape,
        IntervalTrigger(weeks=1),
        id="weekly_scrape",
        coalesce=True,
        max_instances=1,
    )
    # Outscraper: biweekly on the 1st and 15th at 1:00 AM UTC.
    # 70 reviews × 107 restaurants × 2 runs/month = 14,980 records/month (~$44.94).
    # CronTrigger with day='1,15' fires on both dates; no drift vs IntervalTrigger.
    scheduler.add_job(
        run_outscraper_scrape,
        CronTrigger(day="1,15", hour=1, minute=0, timezone="UTC"),
        id="outscraper_scrape",
        coalesce=True,
        max_instances=1,
    )
    # One-time backfill: fires 10 seconds after startup. Fetches 200 reviews for
    # any restaurant whose earliest outscraper record postdates May 25, 2026.
    # run_outscraper_backfill() is a no-op if all restaurants already have that history.
    scheduler.add_job(
        run_outscraper_backfill,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=10),
        id="outscraper_backfill",
        max_instances=1,
    )
    # SBA loans: Sunday 1:30 AM UTC (after Outscraper, before scoring at 5 AM).
    scheduler.add_job(
        run_sba_loans_scrape,
        CronTrigger(day_of_week="sun", hour=1, minute=30, timezone="UTC"),
        id="sba_loans_scrape",
        coalesce=True,
        max_instances=1,
    )
    # Property tax: Sunday 2:00 AM UTC (after SBA loans, before scoring at 5 AM).
    scheduler.add_job(
        run_property_tax_scrape,
        CronTrigger(day_of_week="sun", hour=2, minute=0, timezone="UTC"),
        id="property_tax_scrape",
        coalesce=True,
        max_instances=1,
    )
    # Delivery platforms: Monday 5:00 AM UTC — after health inspections and TABC
    # (Monday 4:00-4:30 AM UTC). Runs on a separate day from Sunday scrapers so
    # the weekly scoring cycle has complete signal data before scoring at 5 AM daily.
    scheduler.add_job(
        run_delivery_platforms_scrape,
        CronTrigger(day_of_week="mon", hour=5, minute=0, timezone="UTC"),
        id="delivery_platforms_scrape",
        coalesce=True,
        max_instances=1,
    )
    # Backtesting outcome tracker: Sunday 3:00 AM UTC — checks prospective cohort
    # restaurants for 90/180-day outcomes. Runs after Outscraper (1 AM) so fresh
    # review data is available for review-gap checks.
    scheduler.add_job(
        run_backtest_outcome_tracker,
        CronTrigger(day_of_week="sun", hour=3, minute=0, timezone="UTC"),
        id="backtest_outcome_tracker",
        coalesce=True,
        max_instances=1,
    )

    # atexit fires on SIGTERM/SIGINT and normal process exit.
    # Equivalent to IHostedService.StopAsync() — gives in-flight jobs a chance to finish.
    atexit.register(scheduler.shutdown, wait=True)

    scheduler.start()
    logger.info(
        "Scheduler started — jobs: %s",
        [j.id for j in scheduler.get_jobs()],
    )

    # Heartbeat loop — keeps the main thread alive and signals liveness to Docker.
    # touch() updates the file's mtime without changing content; the health check
    # reads the mtime to confirm the loop is still running.
    while True:
        HEARTBEAT_FILE.touch()
        time.sleep(HEARTBEAT_INTERVAL)


if __name__ == "__main__":
    main()
