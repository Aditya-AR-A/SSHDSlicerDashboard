
# Dashboard chart correctness and verification

The existing Dash pages, Bootstrap grid, chart IDs and user filters are preserved.

## Current Pending Review

Pending Review has its own callback and in-memory capture. It requests every page of `/api/dashboard/annotator-efficiency`, role 2, from 2020-01-01 through the current source calendar date. The end date covers both India and upstream Asia/Shanghai boundaries. A scoped `Cache-Control: no-cache` header requests revalidation without disabling other caches.

Captures expire after 60 seconds. Initial load, dashboard return, Refresh and workflow updates fetch again; mapping saves invalidate the capture. The existing one-minute workflow poll observes external review changes. This dashboard has no local review-status mutation controls. Future controls must call `invalidate_pending_review()` and trigger `workflow-sync-store` after a successful status mutation.

Generation checks prevent older requests from replacing newer captures. Requests spanning midnight do not reuse the previous day's in-flight capture. Pagination rejects repeated IDs, incomplete totals, changing totals and invalid metrics. Canonical mappings are reapplied on every read; distinct accounts mapped to one person are summed once. Failed reads replace the chart with an unavailable state, with no historical or disk snapshot substitution. During refresh, previous values remain dimmed and explicitly labeled as previous.

The chart reflects the latest response returned by the source API. Backend replication or caching cannot be independently eliminated by a dashboard request; the audit compares values against that exact API response.

## Shared guards and corrected calculations

`plots/guardrails.py` validates finite values, categorical keys and daily dates, preserves zero versus unavailable, and isolates rendering errors. `pages/components.py` gives graphs measurable responsive dimensions. Dense charts scroll within the card; full usernames and decoded numeric values remain accessible in expandable tables. Tooltips are bounded to the viewport. Empty/error axes are hidden and multiline titles have reserved space.

Time-series gaps remain unknown, and cumulative lines use straight segments. Donut components must match their declared total. The recorded batch rework distribution counts identified submitted batches and unique recorded returns; it no longer guesses batch counts from hours or task counts. Missing assignment dates remain Unknown. KPI sparklines are omitted unless real trend evidence is supplied. The date-scoped pending KPI is labeled **Pending Review (Period)**, separately from the current queue chart.

## Reproduce checks

Run from the repository root in separate terminals:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -u scripts/dashboard_chart_preview.py
node scripts/dashboard_browser_audit.mjs
node scripts/dashboard_browser_audit.mjs lifecycle
node scripts/dashboard_browser_audit.mjs labels
.\.venv\Scripts\python.exe scripts/audit_pending_review.py
```

The preview substitutes only synthetic data sources while retaining the actual page layouts and callbacks. It does not read production credentials or storage. The browser runner requires Node 22+ and locally installed Chrome; results and screenshots go to `data/reports/chart-qa/`. Its matrix covers main/daily/user routes, mobile/tablet/laptop/desktop, empty/one/many/long users, zeros, extreme values and API failures. It fails on network/runtime errors, page overflow, clipped titles or clipped outside totals. Lifecycle checks exercise slow refresh, failure, navigation, workflow-triggered refresh, real mouse hover and exact value access. Label checks hide and restore review stages at mobile and desktop widths, asserting the visible total and numerical axis range.

The separate pending audit uses configured credentials for read-only API requests and local user mappings. Its saved comparisons identify that mapping source. It does not perform review mutations. Restart the normal Dash server after Python changes, then refresh the browser.

Verified on 2026-10-08: 287 tests and 26 subtests passed. The browser matrix passed 39 scenarios, plus nine targeted KPI zero/extreme/failure scenarios, with no runtime/network errors, page overflow or clipped titles. Slow refresh, failure replacement, navigation return, workflow-triggered refetch, bounded pointer hover and exact value access passed. The live pending capture contained 79 accounts; all 42 mapped user/stage comparisons matched the API durations and counts.

## Daily rollover correction

The 7 October daily file retained a 16:43 capture with Aditya at 02:52:59, although a later verified capture and the latest dated API evidence gave 05:00:36. The report had frozen yesterday's afternoon observation and skipped source reads even on Refresh.

Yesterday now receives an explicit end-of-day reconciliation on entry and manual refresh. Its dated task evidence is unioned with previously recorded submissions; tasks that have since moved to another date remain retained. The merge accepts this update only with a matching report date, next-day observation/capture timestamp and `day_end_reconciliation` marker. Earlier closed dates and verified audit imports retain their existing policy. Current database account mappings are used, including SSHD-S-Aditya5.

Work and day-comparison charts use the same per-person summary calculation and zero-output roster. The 30-day trend also sums duplicate person summaries rather than overwriting them. Regression checks cover late submissions, retained disappeared tasks, repeated reconciliation, database persistence and source failure labels.

The corrected 7 October capture was saved and read back from both local and database storage with Aditya at 05:00:36. Verification after this correction: 293 tests and 26 subtests passed; the actual repaired capture rendered at 390, 768, 1366 and 1920 pixels without layout/runtime errors. `scripts/audit_daily_report.py 2026-10-07` performs a separate read-only source comparison using configured database mappings, with local mappings as fallback.

## Outside hour totals

Assigned Hours, Pending Review, daily Work and work-composition bars show one numeric hour total outside each stack. Completed Hours and selected/previous-day bars show their own outside values. Exact durations remain in hover details and value tables. The shared hour-axis helper reserves 10% above the largest visible stack or independent hour series; excluded old rework remains outside work totals. Missing values have no invented total, and zero values remain visible as `0.00h`.

Legend changes recompute stacked totals and numerical headroom from the visible stages. Ratio charts keep their existing percentage scale. The local regression suite passes 336 tests and 26 subtests, including tiny/large values, unknown days, excluded old rework, both themes and native outside labels. The updated 39-scenario browser matrix passes without clipped totals or layout/runtime errors. Mobile/desktop legend checks verify the total changes from `3.50h` to `1.50h` and back, with axis limits changing from 3.85 to 1.65; extreme totals retain compact notation after legend changes.
