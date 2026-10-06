# Reporting redesign plan

Updated: 6 October 2026 (Asia/Kolkata). Target: `E:/DEV/scorer`.

## Agreed scope and implementation order

Work takes place on `testing/reporting-phase-one`; keep `main` unchanged. Existing local modifications to the batch-return ledger, batch ledger, and dashboard snapshot are unrelated and must remain out of feature commits.

Implement and verify these phases in order:

1. Daily Report.
2. Individual User Report.
3. Main Dashboard simplification and approval trend.

Only Phase 1 is currently authorized for implementation. Retain the Dashboard's daily chart and Today/Yesterday tabs through Phases 1 and 2 using shared builders; remove their Dashboard presentation in Phase 3.

### Phase 1 implementation checkpoint

Implemented on the testing branch: `/reports/daily`, six daily KPIs and day-over-day comparison, the reused work chart and both tables, 30-day user comparison, work-composition trend, and incremental dated evidence retention. Shared preparers keep chart, table, and KPI totals consistent. Earlier dates show coverage gaps.

Validation: the full test suite passed (159 tests plus 27 subtests), followed by 33 focused plot, page, and refresh checks after the final adjustments. An isolated offline fixture preview passed desktop (1440px) and mobile (390px) checks for navigation, theme switching, legend toggling, chart resizing, and table scrolling. Live upstream and database services were not used for these checks. The existing batch tracker test now persists only in a temporary directory.

Phases 2 and 3 remain planned and unimplemented. Daily history is captured during application refreshes; no unattended scheduler was added.

### Accepted history change

Complete historical backfill is not a prerequisite. Use the verified records already available and begin retaining daily evidence from today or yesterday. The local verified audit for 5 October 2026 can seed history. Missing earlier days remain unavailable, not zero. The 30-day chart will gradually fill as daily evidence is captured.

Collection occurs when the application refreshes; it is not a new unattended scheduler. Show capture time and coverage. Do not promise that unobserved submissions are recovered from a task's latest timestamp. Preserve observed task/day evidence across refreshes so later resubmissions cannot erase an earlier captured day's output. Use durable database storage where configured and local persistence otherwise. Repeated refreshes must be idempotent.

## Repository findings

The site is a Python Dash application with Plotly, Dash Bootstrap Components, pandas, and MongoDB/PostgreSQL persistence. `api/index.py` exposes the Flask server. Vercel routes all requests to that server. There is one dashboard layout and no existing page router or sidebar.

| Area | Existing file/function | Treatment |
| --- | --- | --- |
| Application/header/filters | `src/slicing_dashboard/app.py` | Small stable routing shell; preserve visual style |
| Dashboard callback | `update_dashboard` (13 outputs) | Guard inactive pages; separate new report loading/rendering |
| Coordinated loading | `_dashboard_sources`, `_refresh_scope` | Preserve shared reads and dependency ordering |
| KPI renderer | `plots/kpi_cards.py::_make_kpi_card` | Expose reusable renderer, optional real sparkline |
| Today's Work plot | `app.py::_build_work_chart`, `plots/error_rework_chart.py` | Reuse stacked Fresh/Same-day/Old Rework chart |
| Today/Yesterday tables | `app.py::_render_tab`, `plots/tables.py::create_table` | Extract prepared daily table logic and reuse renderer |
| Authoritative daily loader | `data_manager.py::get_todays_work_df` | Reuse verified evidence and exact-date fallback |
| Upstream evidence capture | `processing/daily_work_source.py` | Share one inventory across daily sections |
| Classification | `processing/daily_work.py::aggregate_daily_work` | One definition for all reports |
| Chart theme | `plots/theme.py` | Reuse colors, formatting, templates; add stable colors |
| Current user roster | `app.py::_available_user_names`, DataManager mappings | Union known users and honor exclusions/aliases |
| Settlement boundaries | `management/periods.py` | Reuse India dates and current saved period |
| Existing individual report | `reporting/individual.py` | Preserve Excel behavior; separate web report preparation |
| Approval transitions | `processing/transitions.py` | Do not use inferred events as approval throughput |
| CSS/theme | `assets/style_v2.css`, `assets/theme.js` | Extend current styles, no visual redesign |

Current Dashboard: seven overview KPIs; Rework Ratio and completed-work-by-user charts; Pending Reviews, Today's Work, and a user legend; Assigned/Rework inventory; Today, Yesterday, Settlement Overview, Slice Data Overview, Settlement Management, and User Mapping tabs.

## Data flow and definitions

Browser interactions use Dash callbacks, not separate reporting REST endpoints. DataManager already has a refresh scope that memoizes source reads and shares a DailyWorkSource inventory between daily components.

| Data | Existing upstream query | Purpose |
| --- | --- | --- |
| Task inventory | `/api/tasks/overview` | Task ID, account/person, latest `slice_submitted_at`, batch, video seconds |
| Group scope | `/api/dashboard/batches` | Scope the inventory |
| Individual return notices | `/api/requests` | Verify rework |
| Batch returns | `/api/requests/batch-return-history` | Verify preceding returns |
| Batch review history | `/api/review/reviewed-batches` | Distinguish same-day vs old rework |
| Overview | `/api/dashboard/overview` | Dashboard status summaries/funnel |
| User summaries | `/api/dashboard/annotator-efficiency`, `/api/dashboard/annotator-efficiency-overall` | Completed/submitted/rework/pending status metrics |
| Review actions | `/api/review/reviewed-tasks` | Possible verified approval history in Phase 3 |

Work is submitted **video duration**, not employee labor time:

```text
Total Work = Fresh Work + Same-day Rework + Old Rework
Reworks = Same-day Rework + Old Rework
```

The separate Today's Rework card means same-day rework. Never add `Reworks` and `Old Rework` together. Deduplicate tasks within each day's captured evidence; a task can contribute again on a different day if it is resubmitted and observed. Approvals, updates, and pending rework are not submitted work.

Naive upstream timestamps use Asia/Shanghai and are converted to Asia/Kolkata by the existing daily classifier. Internal durations remain seconds; display hours or HH:MM:SS. Date ranges are inclusive: the 30 calendar days end on the report date and start 29 days earlier.

Current inventory has only latest submission timestamps and cannot reconstruct every historical attempt. Existing cumulative data measures completed work and fills failures/missing dates with zeros; do not use it for output history. Existing KPI sparklines interpolate with random noise; new reporting must use real points or omit the sparkline.

Local mappings contain 73 aliases and approximately 12 non-system people. Aggregate aliases to people; exclude system/exempt/unassigned labels and preserve unresolved named accounts distinctly. There is no verified stable employee ID. Mapping edits must refresh report directory options.

## Phase 1: Daily Report

### Layout and routing

Add `/reports/daily` and a Daily Report link alongside Dashboard in the current header. Keep the existing Dashboard mounted so navigating does not destroy management editors. Report callbacks run only on the active report route. Keep Dashboard user selection separate from report comparison visibility.

Layout:

1. Report date selector, Today action, refresh/status, India timezone.
2. Six responsive KPI cards with day-over-day change under Total Work.
3. Existing stacked Today's Work chart.
4. Full-width 30-day user comparison.
5. A compact optional team work-composition trend.
6. Separate selected-day and previous-day table sections, reusing existing table rendering with numeric duration sorting and horizontal scrolling.

Default report date is India today. Historical selections use explicit dates instead of misleading Today/Yesterday labels. Daily date scope is separate from the Dashboard's settlement filters.

### KPI requirements

| KPI | Calculation |
| --- | --- |
| Today's Total Work | Sum `Total Duration` |
| Today's New Work | Sum `New Videos (First Time)` |
| Today's Same-day Rework | Sum `Same-day Rework` |
| Today's Old Rework | Sum `Old Rework` |
| Yesterday's Total Work | Previous-day total using the same rule |
| Active Users Today | Distinct people with submitted tasks |
| Day-over-day change | Today minus previous day; percentage only with an available positive denominator |

Cards show video duration in HH:MM:SS; detailed tables include task counts and numeric hours with exact-duration tooltips. Label incomplete current-day comparisons "today so far vs recorded previous day". Defer approval/completion KPIs until actual event dates are verified.

### Chart requirements

| Chart | Measures / usefulness | Fields | Data availability |
| --- | --- | --- | --- |
| Existing daily stacked bar | Per-person output composition | Person, fresh/same-day/old seconds, total seconds, account IDs | Already available |
| 30-day person comparison | Daily workload distribution and variation | Date, person, total seconds, coverage | Use retained available days; grow from today/yesterday |
| Team composition trend | Explains output changes due to fresh vs rework | Date, three category sums, coverage | Same shared history |

Comparison: one line per known person, distinct stable colors, legend toggling/isolation, date/person/formatted duration tooltip, responsive layout. Show all approximately 12 known users initially. No top-N preset or horizontal chart scrolling is needed; tables scroll horizontally.

The composition trend is the only proposed extra daily chart. A contribution chart largely repeats the existing bar. No approval funnel or completion rate in Phase 1.

### Backend and reusable pieces

Add a small report service preparing current day, previous day, 30 dated history entries, roster, summaries, long-form series, and daily tables. Metadata includes availability, capture time, source, staleness, error, and coverage. Unavailable series values are null; successful observed zeros are zero.

Use existing daily capture and classifier. Share inventory/notices/returns between current and previous day in one refresh. Preserve already recorded dates instead of scanning each historical day or rewriting history from later current task state. Incrementally retain task/day evidence with stable date/task identities. Seed available verified audits. Use a reporting-specific database collection/table and local files without rewriting the batch ledgers or global dashboard snapshot.

Keep business logic out of chart components. Chart builders take prepared rows. Reuse the existing daily chart, table, theme, status, Bootstrap panels, and KPI card renderer.

### Sequence and acceptance gate

1. Save this revised plan and create the testing branch.
2. Add routing and report shell without changing Dashboard content.
3. Extract shared daily chart/table preparation and place it on Daily Report.
4. Add consistent daily KPIs.
5. Add incremental history retention and coverage reporting.
6. Add 30-day comparison and composition trend.
7. Verify arithmetic, chart/table/KPI parity, aliases, repeated capture/resubmission preservation, zero vs missing, cache failures, India dates, routing, inactive-page requests, and mobile layout.

Phase 1 can pass with partial historical coverage. No fabricated backfill and no requirement to wait 30 days. Only after Phase 1 verification may work begin on Phase 2.

Expected files: modify `app.py`, `data_manager.py`, `db.py`, `plots/kpi_cards.py`, `plots/tables.py`, `plots/theme.py`, and `assets/style_v2.css`; modify `daily_work_source.py` only if necessary. Create `pages/__init__.py`, `pages/daily_report.py`, `pages/components.py`, `reporting/dashboard_reports.py`, `plots/work_trend_chart.py`, and focused data/plot/routing tests. Keep extraction of the existing dashboard minimal; a separate `pages/dashboard.py` can wait if it would create unnecessary churn.

## Phase 2: Individual User Report (later)

Add `/reports/user` and User Report navigation. Put a searchable canonical-person dropdown at the top. Include mapped users with zero work; aggregate all aliases. Use a selection resolver separate from report generation so a future `?user=<person-or-id>` parameter, employee ID, login identity, or permissions layer can replace the selection source. No authentication changes.

Layout: selector/date; seven required KPIs; full-width 30-day trend; today's donut plus settlement and overall scope panels; compact supported derived statistics.

Required KPIs: yesterday total, today total, fresh work, same-day rework, old rework, current settlement-period submitted work, overall recorded work. All use the shared daily dataset. Current period comes from `get_available_periods()`; do not assume a calendar month. Overall is recorded output since the earliest verified coverage, not lifetime unless coverage is proven. Historical financial/paid/completed totals remain separate.

Use one today donut (Fresh / Same-day Rework / Old Rework) with total in the center, plus numeric current-period and overall panels labeled with ranges and coverage. Concentric rings compare overlapping scopes of very different size; progress rings lack target denominators. Do not invent targets.

| Visualization/metric | Required fields | Recommendation |
| --- | --- | --- |
| 30-day individual total line | Date, person, total seconds, coverage | Required; reuse trend builder |
| Optional category traces | Three daily category durations | Hidden by default |
| 7-day rolling mean | Consecutive covered daily totals | Overlay only when coverage supports it |
| Today's donut | Today's three category sums | Required |
| Settlement/overall panels | Daily history, current boundaries, coverage | Required; label partial totals |
| Rework share | (Same-day + old) / total | Compact statistic; zero total is not applicable |
| 7-/30-day averages and best day | Complete covered calendar days | Withhold unsupported metrics; mark partial/current-day values |

Use completed days for stable averages/best day. Avoid heatmaps and duplicate rework charts initially. Preserve the existing Excel individual report; its update/completion/current-state semantics do not supply the new web totals.

Sequence: add selection; reuse shared data; add KPIs/scopes; trend; donut; supported derived statistics; test aliases, zero-work users, scope boundaries, coverage, no duplicate fetch on selection, and Daily Report parity.

Expected files: create `pages/user_report.py`, `plots/work_breakdown_donut.py`, and user report tests; extend shared report service/components/trend/styles and app registration. Preserve Excel contracts.

## Phase 3: Dashboard changes (later)

After both reports pass verification, remove the Dashboard's daily chart and Today/Yesterday tabs. Retain overview KPIs, completed-work/rework summaries, pending reviews, assigned inventory, Settlement Overview, Slice Data Overview, and management tabs. Add a full-width work/approval trend above retained overview charts.

Verified workflow order is Submitted -> Leader -> Auditor -> Admin/Completed. Existing review-duration fields are pending queues, not approved throughput. Existing `transitions.py` synthesizes events from current status and uses some review times as submission dates; do not plot these as actual historical approval events.

Requested lines: Total Work Done, Leader Approved, Auditor Approved, Admin Approved/Completed. Use actual daily event throughput, not a nested funnel. Approvals on a day may exceed produced work because older submissions are being processed. Keep the pending-stage chart to show backlog; differences between limited-range production and approvals alone do not establish backlog.

Required event fields: stable event ID, task/video and submission-cycle identity where available, actual source timestamp converted to India, canonical person, stage/decision/action, previous/current status, consistent video seconds, source coverage. Verify whether review records are task/video/clip-level before assigning duration. Avoid repeated clip records multiplying video hours. Treat error-confirmed completions separately unless business policy explicitly includes them.

Phase 3 gate: verify real action values and upstream event completeness; normalize timestamps/units; deduplicate true event identities; account for returns/repeated cycles; persist verified events if necessary. Missing past approvals are allowed under the agreed coverage policy, but stage mappings and available event dates still must be reliable. If actual history is unavailable, agree on an explicitly labeled submission-cohort current-status alternative before substituting it.

Expected files: create `plots/approval_trend_chart.py`, a verified event reader such as `processing/approval_history.py`, and workflow tests; modify dashboard layout/callback ownership, report service, DataManager, scraper pagination and DB persistence as required. Do not silently change existing inferred transitions/Excel consumers.

## Shared states, responsive behavior, and risks

- Stable shared shell for navigation/theme/status/refresh. Preserve management editor state; inactive pages do not load data. Report user selection and Dashboard filters remain independent.
- Loading: section indicators and previous values retained during refresh.
- Empty: successful observed zero differs from unavailable data. No zero-filled missing dates.
- Error: visible retry message, matching-date stale data only, capture-time label. Do not use general loaders' cross-range fallbacks.
- Coverage: 30 calendar dates remain on the axis with null gaps; show available-day count and earliest retained day. Today is provisional. Collection only sees observed latest submissions unless upstream event history is added.
- Responsive: KPI grid, vertically stacked charts, wrapping legend and controls, top user selector, horizontal table scrolling, duration sorting by numeric values where supported.
- Risk: return-history limits, task timestamps overwritten by rework, alias/remapping changes, lack of stable employee IDs, stale cache metadata, incomplete workflow history, serverless local-file persistence.
- Tests: reuse daily-work, daily-source, management/period, callback-performance, and plot payload tests. Add report history/idempotence/missing-state and navigation tests. Preserve existing Excel regression tests.

No new standalone reporting REST API or chart library is required. No upstream historical backfill is mandatory. New upstream capabilities are conditional future work for submission events or verified workflow history.
