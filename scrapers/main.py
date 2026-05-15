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
from signals.tabc_license import scrape_license
from signals.hours_monitor import scrape_hours
from signals.sba_loans import scrape_sba_loans
from signals.property_tax import scrape_property_tax
from scoring.engine_v2 import compute_scores_v2

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


def run_outscraper_scrape() -> None:
    """Fetch Outscraper review history for all tracked restaurants.

    Runs weekly on Sunday at 1:00 AM UTC — before the daily scoring engine run
    at 5:00 AM — so fresh monthly data feeds into Sunday scores.

    Outscraper is pay-per-use, so weekly is sufficient to capture monthly review
    volume trends without unnecessary API charges.
    """
    logger.info("[outscraper] job started")
    with Session(engine) as session:
        for r in _get_restaurants(session):
            rid = str(r.id)
            try:
                scrape_outscraper_reviews(r.google_place_id, r.name, rid, session, city=r.city)
                logger.info("[outscraper] %s — OK", r.name)
            except Exception as exc:
                logger.error("[outscraper] %s — FAILED: %s", r.name, exc)
    logger.info("[outscraper] job complete")


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
    # CronTrigger pins the outscraper job to Sunday 1 AM UTC so it always runs
    # before the daily scoring job at 5 AM, feeding fresh monthly data into scores.
    # IntervalTrigger would drift relative to start time; CronTrigger does not.
    scheduler.add_job(
        run_outscraper_scrape,
        CronTrigger(day_of_week="sun", hour=1, minute=0, timezone="UTC"),
        id="outscraper_scrape",
        coalesce=True,
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
