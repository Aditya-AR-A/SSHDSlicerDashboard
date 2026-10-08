# Data capture and recovery on Vercel Hobby

Plots should use prepared data from their owning source, rather than fetch or
repair values inside a chart builder. `processing/source_policy.py` centralizes
the 60-second reuse policy and serializes identical source queries. Daily charts,
tables, comparisons, calendars and scope sums share the same submitted-video
definition: fresh work plus same-day rework; old rework is separate.

## What runs without an open browser

`POST /api/data-refresh` invokes one source registry:

| Source | Consumers |
| --- | --- |
| configuration | Account aliases, exclusions and settlement boundaries |
| daily | Daily Work, comparisons, trends, User Report, calendar and scope sums |
| overview / efficiency | Dashboard totals, individual breakdown and completion trends |
| assignable_pool | Current assignable duration and task count from `/api/slice/pool-inventory` |
| assignments / rework | Current assigned queues |
| pending_review | Current Leader / Auditor / Admin review queues |
| batches / returns | Batch ratios and assignment evidence |
| completion | User Report settlement completion cards |
| workflow | Approval history, approval plots and inbox evidence |

Every source is forced to read again. A failure doesn't stop subsequent sources
or advance that source's last-success time. Complete daily observations are
saved first, including a repeated reconciliation of yesterday. Evidence is merged
by task/day identity, so tasks missing from a later inventory aren't subtracted.
Missing captures and failed reads never become verified zero-work days.

Assignable work is the normal plus urgent unassigned pool. The API supplies hours,
which are converted to video seconds once for the existing UI. Overview/funnel
totals, date filters and person filters never override this current inventory.
Missing pools, unexpected scope and invalid numbers fail the capture. A failed
read displays unavailable and discards the previous pool capture; a successful
zero remains zero. The application's authenticated client owns the session cookie.

Health checkpoints use shared MongoDB/PostgreSQL storage. Local installations
without a configured database use SQLite; Vercel rejects that fallback. A shared
330-second lease limits concurrent jobs. Atomic checkpoint publication rejects an
older attempt after a newer attempt has published. A killed job leaves retained
evidence intact; the lease expires and later calls retry. Deployments and processor
versions invalidate old health confirmations. This lease protects collector jobs;
interactive reads also use their source locks and dated evidence merge rules.

Warm workers reread MongoDB account mappings and periods within 60 seconds.
Mapping changes discard prepared report/review caches. Assigned queues retain raw
tasks and apply current mappings each time, matching Pending Review and completion.
Database outages preserve the last valid mappings and fail the configuration health
check. The dashboard labels those mappings unverified even if the API heartbeat
succeeds. Failed database connection checks expire after 60 seconds and retry;
temporary outages don't require restarting warm workers. The mapping editor
currently requires MongoDB, as before.

Passing midnight does not establish completeness. Historical captures lacking an
end-of-day reconciliation are explicitly marked incomplete. User scope sums remain
partial and completed-day averages omit unreconciled days. Verified audit imports
retain their existing policy. Earlier closed history cannot be reconstructed safely
from the current inventory and isn't rewritten by ordinary refreshes.

## Activate in production

1. Deploy the code with the existing source credentials and a shared `MONGO_URI`
   (or the supported PostgreSQL backend for evidence). Set `CRON_SECRET` to a long,
   randomly generated value. Put the same value in the scheduler's
   `Authorization: Bearer <secret>` header. Never put the secret in the URL.
2. Enable Python Fluid compute and verify the function's maximum duration in Vercel
   Project Settings is sufficient for a complete scan, up to Hobby's supported
   limit. The existing legacy build configuration is preserved. Check a production
   test invocation's duration; don't assume local performance matches Vercel.
3. Schedule `https://YOUR-PRODUCTION-HOST/api/data-refresh` every five minutes.
   Use POST with that authorization header. The daily Vercel job already specified
   in `vercel.json` runs at 19:00 UTC, or 00:30 India time the following day. It is
   a fallback, not the frequent capture schedule. Hobby can delay it within its
   scheduled hour and does not retry failed invocations automatically.
4. Monitor authorized `GET /api/data-health` independently of refresh requests.
   A source is overdue after ten minutes without a successful scheduled capture.
   Configure the scheduler/monitor's failure notifications in its own account.
   Keep health monitoring active even if a refresh job is disabled or runs long.
5. Check the first capture: both dates, all source states, database persistence,
   timing, and a sample person's chart/table total. No deployment or scheduler
   account has been changed by the local implementation.

[Vercel Hobby cron limits](https://vercel.com/docs/cron-jobs/usage-and-pricing),
[cron authorization and retries](https://vercel.com/docs/cron-jobs/manage-cron-jobs),
and [function duration limits](https://vercel.com/docs/functions/configuring-functions/duration).

### Scheduler choices

* [cron-job.org](https://cron-job.org/en/) supports free frequent jobs and custom
  authorization headers. Its [standard timeout is 30 seconds](https://cron-job.org/en/faq/).
  Measure the deployed scan before choosing it. If a full scan exceeds the timeout,
  separate source groups using `?sources=daily`, `?sources=overview,efficiency`, etc.,
  stagger their execution, and ensure each group finishes within the limit. All
  source groups still need to run every five minutes. If even one source exceeds
  that limit, use a longer-running scheduler/worker; don't detach an untracked
  background thread inside a serverless request.
* `.github/workflows/capture-data.yml` is an opt-in alternative with a longer HTTP
  timeout. Set repository secrets `DASHBOARD_DEPLOYMENT_URL` and
  `DASHBOARD_CRON_SECRET`, and repository variable `ENABLE_DATA_CAPTURE=true`.
  It runs on the default branch and can also be invoked manually. Five-minute runs
  can exceed a private repository's [free runner-minute allowance](https://docs.github.com/en/actions/concepts/billing-and-usage).
  Check the repository's billing budget before enabling it. [Scheduled Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
  can be delayed; public repositories can have schedules disabled after inactivity.
  The Vercel fallback and independent health monitor remain necessary.

## Recover one source, without redeploying

Run the protected refresh endpoint with `?sources=daily` or a comma-separated
selection from the table above. An operations client can retry failed sources
while retaining successful captures. The response contains only source names,
scope, times and error classes, never credentials or task-level records.
Configuration is checked on every retry, including a targeted retry.

| HTTP status | Meaning |
| --- | --- |
| 200 on refresh | Every requested source succeeded; inspect overall `ok` for other sources |
| 200 on health | All registered sources are recent, current-version and successful |
| 503 | Failed, absent, overdue source or persistence failure |
| 409 | Another collector owns the lease; retry after it completes or expires |
| 401 | Missing or incorrect secret (missing server configuration also fails closed) |
| 400 | Unknown or empty source selection |

Responses use `Cache-Control: no-store`. A health read never contacts the source
API or changes captures. For a server/terminal install, the same service is:

```bash
slicing-dashboard data-refresh
slicing-dashboard data-refresh --source daily
slicing-dashboard data-refresh --health-only
```

## Limits of recovery

Capture health establishes that a source was read successfully and evidence was
retained. It doesn't prove that upstream data is correct. The source exposes a
task's latest submission timestamp, so submissions overwritten before any capture
cannot be reconstructed exactly. Frequent captures reduce that loss window; they
cannot eliminate it. Guaranteed historical completeness requires an upstream
append-only submission/event feed. Missing old evidence needs a verified dated
export or audit, not a guessed total or a cache reset.

## Verification

The local suite passes 336 tests and 26 subtests. Recovery tests exercise source
failure isolation, retained success times, process restart, source expiry, deployment
version changes, concurrent jobs, old checkpoint rejection, storage failure,
endpoint authorization, targeted retries, raw-queue remapping and database reconnects.
Pool inventory regressions cover the supplied zero response, normal plus urgent
hours, scope validation, missing/invalid metrics, expiry, expired sessions, forced
refresh sharing, user/date independence and removal of overview/funnel overrides.
Twelve browser checks cover dashboard, Daily Report and User Report at 390, 768,
1366 and 1920 pixels, including an unreconciled historical capture warning.
These checks use local fixtures; the production scheduler and scan duration still
need verification after deployment.
