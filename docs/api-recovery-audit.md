# API and dashboard recovery audit — 8 October 2026

The [upstream API documentation](http://120.79.192.226/docs) and its OpenAPI
schema were read directly. The supplied authenticated POST request works.
`scorer` owns the current Dash dashboard and uses `slicing_dashboard` in MongoDB.
The separate `SSHDSlicerAnalytics` project uses `Slicer_Analytics`, whose task
records currently end on 28 September; it is not the newer reporting database.

| Endpoint | Useful application |
| --- | --- |
| `POST /api/dashboard/annotator-efficiency` | Per-account completed, submitted, pending Leader/Auditor/Admin, error review and rework counts/video seconds. Use `role=2`, `leader_id=803`, and all pages. Implemented as independent dated captures. |
| `/api/dashboard/annotator-efficiency-overall` | Exact-range aggregate cross-check and summary cards; avoid summing the same all-time totals across daily captures. |
| `/api/dashboard/task-duration-summary` | Aggregate duration reconciliation by requested statuses. Its documented date boundaries use Asia/Shanghai. |
| `/api/dashboard/slice`, `/api/dashboard/overview` | Summary and breakdowns with workflow, group, account, reviewer, batch and status filters. |
| `/api/review/reviewed-batches` | Actual dated approval/return actions; cursor pagination, date/member/reviewer/group/batch filters. Keep this as the source of the approval lines. |
| `/api/review/reviewed-tasks` | Task-level review audit evidence, independently of batch-duration estimates. |
| `/api/slice/tasks` | Current assigned/rework inventory with status/account/group and cursor filters. It exposes latest task state, not a complete submission-event archive. |
| `/api/slice/pool-inventory` | Current normal/urgent assignable pool; already used by the dashboard. |
| `/api/dashboard/user-activity` | Account/date/role activity diagnostics. Its values need semantic verification before using them as video-output measurements. |

## Why October 6 and the review lines can disappear

October 6 was not deleted from `slicing_dashboard.daily_work_reports`. It retains
1,468 task/day observations. Under the existing total-work policy, its saved
capture has 111,392 video seconds of fresh plus same-day rework, and 23,605 seconds
of old rework separately. This capture was taken on October 6 and has no later
end-of-day reconciliation; it remains a retained observation, not a certified
full-day total.

Direct audits repeatedly reproduced MongoDB connection/read timeouts. The prior
daily reader discarded the entire result if a large task-evidence download
failed. The workflow callback also downloaded every projected event to prepare
the small approval series, and workflow ingestion/projection failures on a fresh
page left that series unavailable. Retained dated approvals do exist.

The revised daily reader fetches small summaries first and downloads full reports
one at a time. A failed full read retains summaries and labels them saved; a warm
worker retains its richer task evidence. Summary fallbacks are never written
back as task evidence. Approval preparation queries dated batch approvals only,
and the trend loader reads those records independently of workflow ingestion.
An incomplete checkpoint cannot turn unobserved approval days into verified zero.

These reproduce failures in the local code against the configured shared MongoDB.
The deployed dashboard URL was not provided, so production UI behavior has not
been independently inspected.

## Backfill and ongoing capture

The backfill completed for **1 September–8 October 2026**, with **38 captures and
3,040 account/day rows**, using the supplied API session. Each day was saved and
read back before the script advanced. Records use kind `efficiency-capture` in
`workflow_records`, scoped by source URL, leader and role. They are independent of
the replaceable global dashboard snapshot and preserve every metric in the API.
Authentication cookies are never written into these records.

The October 6 response contains 582 completed videos / 54,055 seconds completed,
2,055 seconds pending Leader review and 47,142 seconds pending Auditor review.
The upstream day is Asia/Shanghai. These are **current statuses of the source's
date-filtered records**, not approval-event throughput on October 6. The main
trend labels completion as source-date status; the existing approval traces
continue to use actual dated batch actions, with their duration estimates.

Duplicate accounts, mismatched dates, incomplete pagination, missing metrics,
nonfinite/negative values and changing pagination totals reject a capture and
preserve its last valid version. Mappings and exclusions apply when reading, so
changing an account alias does not require another API backfill. Real zero values
remain zero; missing task-history dates remain unknown.

Run or resume a backfill from the scorer workspace:

```powershell
.venv/Scripts/python.exe scripts/backfill_efficiency.py --start 2026-09-01 --end 2026-10-08
```

It logs in with configured credentials by default. An optional `PIPELINE_AUTH`
environment variable accepts an existing authorized session without saving it.
`--force` refreshes already retained historical statuses. The report is written to
`data/reports/efficiency-backfill.json`. A failed day causes a nonzero exit code;
rerunning resumes successful historical captures.

The existing scheduled `efficiency` source also persists today and yesterday.
The interactive trend refresh reads the latest saved captures without waiting
for live ingestion; source failures retain saved captures and are labeled. The
existing scheduler must remain active to renew today and yesterday.
Code changes need a restart/redeployment before those new readers and scheduled
capture steps run in the deployed dashboard.

## Verification

An independent database read confirmed all 38 captures and 3,040 account/day
rows. The selected 30-day range contains 483 retained dated batch approvals;
all of those records have a batch-duration estimate. The rebuilt October 6 row
contains 111,392 seconds saved work, 104,634 seconds Leader approval estimates,
3,792 seconds Auditor approval estimates and 54,055 seconds completed status.
Admin approval remains unknown for that date. These measures have different
event/status definitions and are not additive.

The full suite passed: 360 tests and 26 subtests. The standalone interactive
preview is `data/reports/efficiency-recovery/dashboard-trend.html`, with checked
trace values in the adjacent `verification.json`.
