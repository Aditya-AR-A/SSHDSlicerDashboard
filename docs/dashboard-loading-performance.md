# Dashboard saved first paint

The dashboard previously returned KPIs, completion, rework and assignments in
one callback. Batch history and the full snapshot write delayed every panel in
that response. A retained refresh profile from October 12 measured 12.5851
seconds, including 4.6703 seconds in snapshot persistence. These are source and
server timings, not a production browser comparison.

The dashboard now prepares five independent compact captures: summary,
completion, rework, assignments and pending review. The scheduled collector
includes `chart_panels` and persists the successful captures in the existing
snapshot. The prepared range matches the current settlement period, including
its configured end date; current queue captures are scoped to the local day.

On page entry, a quick callback reads the saved captures without accessing the
source API or database. Saved charts render while each panel runs its own live
check. The saved renderer has no live-store Input, so Dash does not hold its
first paint for that check. Changing people or theme renders the retained
capture without fetching data again. Each panel displays its capture time and
whether a check is pending or failed.

Interactive panel checks update memory without waiting for the full snapshot
write. Scheduled collection still persists the snapshot. A failure keeps the
last successful chart and its timestamp. Missing captures show a loading or
unavailable state. Exact range, source account, mapping and processor version
keys prevent borrowing a different scope; request IDs reject late UI results.
The snapshot writer owns a copy so concurrent successful captures can continue.

## Verification

- Full Python suite before the final capture-integrity additions: 416 tests and
  26 subtests passed.
- Synthetic localhost browser audit after restarting the server, with rework
  deliberately delayed by ten seconds: saved charts appeared in 2.008 seconds,
  completion updated in 2.572 seconds, and rework updated in 12.154 seconds.
- The same audit verified theme changes during loading and retention after a
  failed live check. No runtime or network errors were reported.
- Regressions cover saved reads without source calls, source independence,
  scope isolation, selection totals, snapshot reload, partial scheduled failures,
  concurrent snapshot writes and mapping changes during capture.

The browser timings are local synthetic measurements, not production guarantees.
An earlier run measured 5.786 seconds for saved charts; another did not observe
a completion update before its timeout. The subsequent checks passed, including
the fresh server check above. The audit records callback ordering and visible
statuses if it fails, to make further investigation concrete.

After deployment, one successful scheduled collection populates the new durable
captures. Previously unseen custom date ranges load independently on their first
visit; subsequent visits can use the prepared in-memory result. Worker startup
still loads configuration and the original snapshot, and Plotly still has its
initial browser cost. Moving those database reads out of startup and mounting
only the active page are further performance work.

Reproduce without production credentials or API writes:

```powershell
.\.venv\Scripts\python.exe scripts/dashboard_chart_preview.py
node scripts/dashboard_browser_audit.mjs panel-latency
```

The browser audit is a standalone fallback for an unavailable in-app browser
connection. It writes `data/reports/chart-qa/audit-panel-latency.json`; failures
write `audit-failure.json` with callback timings and visible statuses.

## Range filter correction

The line chart now reads the exact From/To interval instead of a fixed thirty
days ending at To. Its cache includes both boundaries, and single-day selections
are supported. Each panel starts its live check after returning its saved or
loading figure, preventing a fast live result from being replaced by a slower
first paint. After person or theme changes, the current live result is reapplied
without starting another source capture.

The corrected implementation passed 424 Python tests and 26 subtests. The
isolated browser check passed From changes, To changes, settlement selection,
line-chart boundaries, and rapid range changes with a theme update. Run
`node scripts/dashboard_browser_audit.mjs range-filters` against the fixture.
Use `DASHBOARD_QA_ORIGIN` to test an isolated preview port; this prevents an older
preview process from accidentally serving the browser audit.

## Hosted verification

On October 12, the supplied production URL,
`https://sshd-slicer-dashboard.vercel.app/`, returned HTTP 402 with
`DEPLOYMENT_DISABLED` for both the page and Dash dependencies endpoint. This
prevents inspecting the deployed callbacks or testing production filters.
The local fixes have not been deployed. The account owner needs to resolve the
hosting restriction in Vercel before production verification can continue; the
response alone does not identify the underlying account or usage condition.

The owner subsequently supplied the usage breakdown: the dashboard consumed
11h 51m of active CPU, representing 97.9% of team CPU usage. Team totals exceeded
both the 4 CPU-hour and 360 GB-hour memory allowances. These totals identify
the project but do not attribute usage to individual callbacks.

Notification polling and bell clicks now issue a separate pending-review
request. Summary, completion, rework and assignment stores receive no update,
so their capture pipelines remain idle. Date changes and full refreshes still
update all five panels. The 40 affected callback, chart-loading and daily-report
tests passed. This change is local and not deployed; there is no measured
production resource saving yet, and it does not clear accumulated Vercel usage.
