# Task: Update CLAUDE.md with this session's standing rules

Read the existing `CLAUDE.md` and MERGE the sections below into it. Preserve all existing
content — especially the NON-NEGOTIABLE data-storage policy (do not weaken, duplicate, or
reword it; the raw_signals points below are corollaries of it, cross-reference rather than
restate). Integrate into existing sections where they fit; only create new headings where none
exist. Show me the diff before writing.

These are durable policies/invariants — not a changelog. Do not add session-specific events
(phantom rows, credit top-ups, one-off counts).

---

## Outscraper run-log semantics

Outcome taxonomy for each restaurant in the batch:
- **completed** — Outscraper returned >=1 review. Writes a `raw_signals` row.
- **no_reviews** — Outscraper queried successfully but returned an empty array. Still writes a
  `raw_signals` row (payload has `reviews_data: []`, `total_reviews_fetched: 0`) per the raw-data
  policy — an empty result is a real, stored signal, never dropped.
- **quota_hit** — internal monthly record cap reached mid-run. No `raw_signals` row.
- **no_credits** — Outscraper account balance exhausted (HTTP 402). No `raw_signals` row.
- **failed** — an exception was raised for that restaurant. No `raw_signals` row.

Status invariant: **`ok` means zero failures AND zero skips** — everything that could land, did.
Any exception or skip breaks `ok`. Status values: `ok`, `throttled` (partial: some landed, some
skipped/failed), `no_credits`, `quota_exhausted`, `failed`, `empty` (no eligible restaurants).

Run-log columns:
- `restaurants_completed` counts only actual reviews-fetched results — a live tally, never the
  size of the eligible set. (Historical rows predating this fix log a flat `86` and are stale;
  do not trust `completed` on pre-fix rows.)
- `restaurants_skipped` = quota_hit + no_credits only.
- `restaurants_no_reviews` and `restaurants_failed` are their own **nullable** columns. NULL on
  historical rows means "not tracked when this run happened" — never backfill with 0.

Reconciliation (SQL-only): for any run written by the current code,
`COUNT(DISTINCT restaurant_id)` in `raw_signals` for that run's date
== `restaurants_completed + COALESCE(restaurants_no_reviews, 0)`.
`failed` and `skipped` are excluded from both sides because they leave no `raw_signals` footprint.
When run-log and raw_signals disagree, **raw_signals is the source of truth.**

Exception isolation: the per-restaurant loop wraps each restaurant in try/except; a single
failure increments `failed` and continues to the next. One bad restaurant must never abort the
batch.

## Outscraper limits — two distinct ceilings

The external account credit balance (402 PAYMENT REQUIRED) and the internal 15,000-record
monthly cap (quota guard, tracked in `outscraper_run_log`) are separate limits. The internal
guard can report budget remaining (e.g. `0/15000 used`) while the account is actually out of
credits. When a run pulls zero, check the Outscraper dashboard balance — not just the internal
quota — before assuming the pipeline is broken.

## Cohort

The prospective cohort is **94 restaurants**. 8 are excluded from every scraper (Google,
Outscraper, hours, scoring) via `WHERE google_place_id IS NULL` — intentional: they lack Google
onboarding, so there's no Google signal to cross-reference. That leaves **86 eligible** per run.
Customer-facing cohort figures must track the real number and never cite a stale count.

## Scheduler reliability (known risk)

APScheduler cron jobs run inside the scrapers container. If the container is down at fire time
(e.g. Docker Desktop off/asleep), the job is silently dropped — no error, no catch-up. Manual
backfill is required after any outage. TODO (not yet implemented): set `misfire_grace_time` and
`coalesce=True` so a short-missed job runs on restart; longer term, move the scheduler to an
always-on host rather than a dev laptop.

## Destructive SQL discipline

Never run UPDATE/DELETE on log tables without first running a SELECT dry-run with the IDENTICAL
WHERE clause and confirming the exact row count. Target rows by UUID primary key (`id IN (...)`),
not by value filters like status or count — those can drift onto legitimate rows when multiple
rows share the same values.

## Windows / PowerShell operational notes

- `psql` is not on the Windows PATH; run it via `docker exec -e PGPASSWORD=... <db-container>
  psql -U admin -d bizhealth -c "..."`.
- PowerShell reserves `<`. Never use `psql -f /dev/stdin < file.sql`. Run migrations with inline
  `-c "..."` or `-f` pointing at a path inside the container.
- Large prompts to Claude Code: pasted input is capped (~5KB) and multi-line paste can corrupt in
  the classic console. Use the standing pattern — drop a `.md` in the project root and tell Claude
  Code to `read <file> and follow the instructions in it`. Prefer Windows Terminal over the
  classic console host for pasting.
