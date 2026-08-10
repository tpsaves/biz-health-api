# Task: Fix Outscraper run-log counting and status logic in main.py

Modify `main.py` — Outscraper run-log counting and status logic **only**. Do NOT touch
scraping, scoring, quota enforcement, or raw_signals storage. Show me the final diff before
writing anything.

## Background
The outscraper run-log status was logging `ok` on runs where zero reviews actually landed
(a restaurant skipped for no credits was still counted as completed). We're fixing the
counting to reflect reality, and the status so that `ok` means zero failures AND zero skips.
Any exception or skip must break `ok`.

## Apply this diff
Reconcile against the real line numbers — the loop over `_get_restaurants` in the outscraper
job function.

```diff
-        completed = 0
-        quota_hit = 0
-        no_credits_count = 0
-        for r in _get_restaurants(session):
+        restaurants = _get_restaurants(session)
+        attempted = len(restaurants)
+        completed = 0
+        no_reviews_count = 0
+        quota_hit = 0
+        no_credits_count = 0
+        failed_count = 0
+        for r in restaurants:
             rid = str(r.id)
             try:
                 result = scrape_outscraper_reviews(r.google_place_id, r.name, rid, session, city=r.city)
                 if result.get("no_credits"):
                     no_credits_count += 1
-                    logger.warning("[outscraper] %s — account has no credits, skipping", r.name)
+                    logger.warning("[outscraper] %s — no account credits", r.name)
                 elif result.get("quota_exceeded"):
                     quota_hit += 1
                     logger.warning("[outscraper] %s — quota exhausted mid-run", r.name)
+                elif result.get("total_reviews_fetched", 0) == 0:
+                    no_reviews_count += 1
+                    logger.info("[outscraper] %s — OK (0 reviews returned)", r.name)
                 else:
                     completed += 1
-                    logger.info("[outscraper] %s — OK", r.name)
+                    logger.info("[outscraper] %s — OK (%d reviews)", r.name, result["total_reviews_fetched"])
             except Exception as exc:
+                failed_count += 1
                 logger.error("[outscraper] %s — FAILED: %s", r.name, exc)

+        accounted = completed + no_reviews_count + quota_hit + no_credits_count + failed_count
+        if accounted != attempted:
+            logger.warning(
+                "[outscraper] invariant breach: attempted=%d accounted=%d "
+                "(completed=%d no_reviews=%d quota_hit=%d no_credits=%d failed=%d)",
+                attempted, accounted, completed, no_reviews_count, quota_hit, no_credits_count, failed_count,
+            )
+
         fresh = outscraper_quota_summary(session)
-        if no_credits_count > 0 and completed == 0:
-            status = "no_credits"
-        elif no_credits_count > 0 or quota_hit > 0:
-            status = "throttled"
-        else:
-            status = "ok"
+        # Status invariant: "ok" means zero failures AND zero skips — everything
+        # that could land, did. Any exception or skip must break "ok".
+        if attempted == 0:
+            status = "empty"
+        elif completed == 0 and no_reviews_count == 0:
+            # Nothing useful landed — name the dominant cause.
+            if no_credits_count > 0:
+                status = "no_credits"
+            elif quota_hit > 0:
+                status = "quota_exhausted"
+            else:
+                status = "failed"          # only exceptions could zero it out
+        elif no_credits_count > 0 or quota_hit > 0 or failed_count > 0:
+            status = "throttled"           # partial: some landed, some didn't
+        else:
+            status = "ok"
+        # NOTE (deferred): restaurants_skipped only carries quota+credits. no_reviews
+        # and failed are logged to stdout but NOT persisted, so completed+skipped
+        # will under-count attempted by (no_reviews + failed). Revisit if run history
+        # feeds the validation study — would need two new columns + migration.
         log_run_status(status, completed, quota_hit + no_credits_count, records_before, fresh["records_used"], session)

     logger.info(
-        "[outscraper] job complete — status=%s completed=%d quota_hit=%d no_credits=%d",
-        status, completed, quota_hit, no_credits_count,
+        "[outscraper] job complete — status=%s attempted=%d completed=%d "
+        "no_reviews=%d quota_hit=%d no_credits=%d failed=%d",
+        status, attempted, completed, no_reviews_count, quota_hit, no_credits_count, failed_count,
     )
```

## Constraints
- Counting and logging only. Do not alter `scrape_outscraper_reviews`, the scoring engine,
  quota-guard behavior, or raw_signals writes.
- Do NOT change the `log_run_status(...)` call signature or the DB schema. `no_reviews_count`
  and `failed_count` stay in stdout only for now — the deferred NOTE comment documents this.
- The diff is against my best reading of the file; the actual variable names and surrounding
  lines are authoritative. If `scrape_outscraper_reviews` returns a different key than
  `total_reviews_fetched` for the review count, use the real key and tell me what it is.
- Preserve existing indentation/style.

## After applying, report back
1. The final diff as written.
2. Confirm the loop is wrapped so a single restaurant's exception increments `failed_count`
   and continues to the next restaurant — it must NOT abort the whole batch.
3. Answer one question by reading the code (do not change anything): when Outscraper returns
   an empty reviews array (0 reviews), does `scrape_outscraper_reviews` still write a row to
   `raw_signals`? Quote the lines that show it. This determines whether the reconciliation
   check should compare raw_signals against `completed` or against `completed + no_reviews`.
