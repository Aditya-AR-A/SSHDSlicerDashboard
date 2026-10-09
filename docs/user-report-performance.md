# User report loading — 9 October 2026

The Team member selector previously started empty and waited for `user-report-store`. That store ran today's and yesterday's live task scans, a full retained-history read, and the settlement completion request sequentially. Names and charts therefore inherited all of those delays.

The initial layout now contains the known roster and an initial selection. Saved report data can add newly observed people, while a still-valid selection returns `no_update` to avoid another unnecessary content render. Names require no report or API request.

The user report reads chart-sized saved history from reachable Mongo replicas with the shared five-second deadline and a 30-second cache. Indexed history bounds preserve older overall totals, and the reader includes the 90-day activity calendar, 30-day trend and average windows. Aliases, exclusions and batch owners use current mappings. Missing days remain unknown. Cached results are isolated from callers, and mapping or settlement changes invalidate the cache.

Interactive refresh reloads the saved work captures; it does not rerun task inventory scans. Existing scheduled daily captures and Daily Report reconciliation maintain those observations. Capture timestamps, incomplete reconciliation and persistence failures remain visible.

Completion keeps its exact settlement-range source query in an independent callback. Its result updates only the completion card and capture note, so it cannot block or remount the work charts. Interactive completion reads update the in-memory cache without waiting for a primary snapshot write; scheduled captures retain persistence. A mismatched date range remains unavailable rather than borrowing another settlement's total.

## Verification

- Full suite: **377 tests and 26 subtests passed**.
- Browser audit with a ten-second report delay: initial names appeared in **1.311 seconds**, before report data arrived.
- Browser audit with a ten-second completion delay: all three charts appeared in **1.830 seconds**; switching people took **0.512 seconds**. Completion updated without remounting the charts.
- All **12 normal route/viewport checks** passed without runtime/network errors or page overflow.
- Real local callbacks after restart: history **0.651 seconds**, report content **0.219 seconds**, completion **1.334 seconds**, completion card **0.009 seconds**.
- Saved user history alone: **0.718 seconds**; history plus page preparation **1.003 seconds**; cached history **0.001 seconds**.
- October 6 retained work still totals **111,392 seconds**. Regressions cover older overall history, aliases and exclusions, batch remapping, exact completion scope, unavailable data and invalid dates.

Reports are in `data/reports/chart-qa/audit-user-latency.json`, `audit-normal.json`, `user-report-history-check.json` and `user-local-callback-check.json`.

Reproduce the browser check against the synthetic local fixture:

```powershell
.\.venv\Scripts\python.exe scripts/dashboard_chart_preview.py
node scripts/dashboard_browser_audit.mjs user-latency
```
