
# Dashboard chart correctness and verification

The existing Dash pages, Bootstrap grid, chart IDs and user filters are preserved.

## Current Pending Review

Pending Review has its own callback and in-memory capture. It requests every page of `/api/dashboard/annotator-efficiency`, role 2, from 2020-01-01 through the current source calendar date. The end date covers both India and upstream Asia/Shanghai boundaries. A scoped `Cache-Control: no-cache` header requests revalidation without disabling other caches.

Captures expire after 60 seconds. Initial load, dashboard return, Refresh and the existing notification/auto-refresh timers fetch again; mapping saves invalidate the capture. The aggregate no longer waits for workflow ingestion to complete. This dashboard has no local review-status mutation controls. Future controls must call `invalidate_pending_review()` and trigger the pending chart directly after a successful status mutation.

Generation checks prevent older requests from replacing newer captures. Requests spanning midnight do not reuse the previous day's in-flight capture. Pagination rejects repeated IDs, incomplete totals, changing totals and invalid metrics. Canonical mappings are reapplied on every read; distinct accounts mapped to one person are summed once. Failed reads replace the chart with an unavailable state, with no historical or disk snapshot substitution. During refresh, previous values remain dimmed and explicitly labeled as previous.

The chart reflects the latest response returned by the source API. Backend replication or caching cannot be independently eliminated by a dashboard request; the audit compares values against that exact API response.

## Shared guards and corrected calculations

`plots/guardrails.py` validates finite values, categorical keys and daily dates, preserves zero versus unavailable, and isolates rendering errors. `pages/components.py` gives graphs measurable responsive dimensions. Dense categorical charts retain horizontal scrolling; tall charts expand with the page. Vertical wheel input scrolls the page over every chart, and wheel zoom is disabled. Full labels and values remain in hover details; the expandable chart tables have been removed. Tooltips are bounded to the viewport. Empty/error axes are hidden and multiline titles have reserved space.

Previous plots stay interactive during a refresh. Loading labels do not intercept input. Plotly redraws no longer build hidden tables or trigger repeated document-wide chart scans, and each size change uses the shared ResizeObserver once. Data requests can still take time: the retained refresh profile showed about 25 seconds each for today/yesterday source scans and a 15-second database history timeout. These waits no longer disable the existing plots.

Chart refreshes and scheduled captures reuse verified account mappings. A newly observed unmapped account triggers one mapping reload; encountering the same account again does not. Mapping discovery uses already captured accounts without requesting the user directory. The Settings mapping editor and an explicit data-health refresh of the configuration source can deliberately reload shared mappings. Configuration failures remain recorded in data health without repeating dashboard warning banners or asking the user to refresh mappings.

Time-series gaps remain unknown, and cumulative lines use straight segments. Donut components must match their declared total. The recorded batch rework distribution counts identified submitted batches and unique recorded returns; it no longer guesses batch counts from hours or task counts. Missing assignment dates remain Unknown. KPI sparklines are omitted unless real trend evidence is supplied. The date-scoped pending KPI is labeled **Pending Review (Period)**, separately from the current queue chart.

## Reproduce checks

Run from the repository root in separate terminals:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -u scripts/dashboard_chart_preview.py
node scripts/dashboard_browser_audit.mjs
node scripts/dashboard_browser_audit.mjs latency
node scripts/dashboard_browser_audit.mjs lifecycle
node scripts/dashboard_browser_audit.mjs labels
.\.venv\Scripts\python.exe scripts/audit_pending_review.py
```

The preview substitutes only synthetic data sources while retaining the actual page layouts and callbacks. It does not read production credentials or storage. The browser runner requires Node 22+ and locally installed Chrome; results and screenshots go to `data/reports/chart-qa/`. Its matrix covers main/daily/user routes, mobile/tablet/laptop/desktop, empty/one/many/long users, zeros, extreme values and API failures. It fails on network/runtime errors, page overflow, clipped titles or clipped outside totals. Lifecycle checks exercise a legend click during a five-second refresh, failure, navigation, workflow-triggered refresh, real mouse hover, absence of chart tables, and wheel scrolling in both directions over a tall 35-user plot. Label checks hide and restore review stages at mobile and desktop widths, asserting the visible total and numerical axis range.

The separate pending audit uses configured credentials for read-only API requests and local user mappings. Its saved comparisons identify that mapping source. It does not perform review mutations. Restart the normal Dash server after Python changes, then refresh the browser.

Verified on 2026-10-08: 287 tests and 26 subtests passed. The browser matrix passed 39 scenarios, plus nine targeted KPI zero/extreme/failure scenarios, with no runtime/network errors, page overflow or clipped titles. Slow refresh, failure replacement, navigation return, workflow-triggered refetch, bounded pointer hover and exact value access passed. The live pending capture contained 79 accounts; all 42 mapped user/stage comparisons matched the API durations and counts.

Refresh interaction follow-up on 2026-10-08: the full suite passed 361 tests and 26 subtests; after refining scheduled configuration reuse, all 55 targeted mapping/health/chart tests passed. The normal/35-user browser matrix passed 24 route/viewport combinations with no runtime or network errors. A real legend click worked during a five-second pending-review refresh, no chart tables were mounted, and wheel input over the 1,350px assignment plot moved page scroll from 1,601px to 1,951px and back. The tall chart viewport matched its full height. Reports are in `data/reports/chart-qa/audit-normal-many.json` and `lifecycle.json`.

## Chart loading follow-up — 9 October 2026

The prior trend callback took 69.6 seconds: today/yesterday task scans and a database history timeout. Both charts also depended on workflow ingestion and on selection state written by the slow KPI callback. Selection now has a pure callback, and the charts depend directly on the existing refresh controls and timers. No new polling timer or ingestion job was added.

The line chart reads saved daily summaries, approvals and completion captures concurrently, with a five-second Mongo read deadline and a 30-second cache. Read-only history prefers reachable replicas; primary writes and live ingestion retain their original database connection. A primary timeout no longer delays healthy replica reads. Saved source timestamps and failure labels remain visible. Pending Review still reads the live aggregate API, which returned in about 0.4 seconds in the live check.

Future captures persist small derived chart fields beside unchanged task/event evidence. Legacy records remain readable without a migration. `scripts/backfill_chart_fields.py --apply` can add those fields using compare-and-set updates; the attempted migration was interrupted by primary connectivity failures and is not required for the verified fast path. No source payload or task history is replaced by chart summaries.

The live saved-history check loaded in 0.964 seconds and built the figure by 1.113 seconds; a cached load took 0.004 seconds. All five traces contain data. October 6 retains 111,392 seconds of work, 104,634 seconds of Leader approvals, 3,792 seconds of Auditor approvals and 54,055 seconds of source-date completed video. The 30-day range ending October 9 contains 465 dated approvals and 29 completion captures through October 8; a missing October 9 capture remains a gap. See `data/reports/chart-qa/fast-trend-check.json`.

The synthetic browser test deliberately delayed KPI/workflow callbacks by ten seconds. Both charts appeared after 4.005 seconds, including initial page/script loading, while the main callback was still loading. A user legend click updated the pending chart in 0.656 seconds during that wait. The refresh/scroll lifecycle and all 24 normal/35-user route and viewport checks also passed, with no runtime or network errors. The full suite passed 371 tests and 26 subtests; snapshots were checked byte-for-byte and were unchanged by that run.

After a final account-remapping refinement, 44 targeted tests and three subtests passed. The normal local dashboard was restarted on port 8050 and its actual callback dependencies checked. A forced history refresh took 0.635 seconds, line rendering 0.058 seconds, and live Pending Review 1.439 seconds. Work, Leader, Auditor and completion traces were populated, and October 6 values matched retained evidence. The latest workflow checkpoint was incomplete, so the Admin trace correctly remained unavailable rather than inventing zero approval days. Results are in `data/reports/chart-qa/local-callback-check.json`. The temporary synthetic QA server was stopped; hosted deployment has not been performed.

## Daily rollover correction

The 7 October daily file retained a 16:43 capture with Aditya at 02:52:59, although a later verified capture and the latest dated API evidence gave 05:00:36. The report had frozen yesterday's afternoon observation and skipped source reads even on Refresh.

Yesterday now receives an explicit end-of-day reconciliation on entry and manual refresh. Its dated task evidence is unioned with previously recorded submissions; tasks that have since moved to another date remain retained. The merge accepts this update only with a matching report date, next-day observation/capture timestamp and `day_end_reconciliation` marker. Earlier closed dates and verified audit imports retain their existing policy. Current database account mappings are used, including SSHD-S-Aditya5.

Work and day-comparison charts use the same per-person summary calculation and zero-output roster. The 30-day trend also sums duplicate person summaries rather than overwriting them. Regression checks cover late submissions, retained disappeared tasks, repeated reconciliation, database persistence and source failure labels.

The corrected 7 October capture was saved and read back from both local and database storage with Aditya at 05:00:36. Verification after this correction: 293 tests and 26 subtests passed; the actual repaired capture rendered at 390, 768, 1366 and 1920 pixels without layout/runtime errors. `scripts/audit_daily_report.py 2026-10-07` performs a separate read-only source comparison using configured database mappings, with local mappings as fallback.

## Outside hour totals

Assigned Hours, Pending Review, daily Work and work-composition bars show one numeric hour total outside each stack. Completed Hours and selected/previous-day bars show their own outside values. Exact durations remain in hover details. The shared hour-axis helper reserves 10% above the largest visible stack or independent hour series; excluded old rework remains outside work totals. Missing values have no invented total, and zero values remain visible as `0.00h`.

Legend changes recompute stacked totals and numerical headroom from the visible stages. Ratio charts keep their existing percentage scale. The local regression suite passes 336 tests and 26 subtests, including tiny/large values, unknown days, excluded old rework, both themes and native outside labels. The updated 39-scenario browser matrix passes without clipped totals or layout/runtime errors. Mobile/desktop legend checks verify the total changes from `3.50h` to `1.50h` and back, with axis limits changing from 3.85 to 1.65; extreme totals retain compact notation after legend changes.
