# Data capture and recovery on Vercel Hobby

Plots should use prepared data from their owning source, rather than fetch or
repair values inside a chart builder. `processing/source_policy.py` centralizes
the 60-second reuse policy and serializes identical source queries. Daily charts,
tables, comparisons, calendars and scope sums share the same submitted-video
definition: fresh work plus same-day rework; old rework is separate.

## What runs without an open browser

On Vercel, `POST /api/data-refresh` durably queues a job and returns **202 Accepted**.
The protected health endpoint reports its progress; acceptance is not proof of
successful capture. A small Node function uses Vercel's supported `waitUntil`
mechanism to hold an HTTP connection to the Python collector after the scheduler
receives its response. Each invocation collects one source and hands the next
step to another invocation. All sources use the same registry:

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

Job checkpoints and the active-job pointer use the same shared database as health.
Repeated triggers reuse the current job; independently requested sources are
added to it. Failed sources do not erase successful captures. Worker connection
failures and source failures create one durable notification per job in the
shared dashboard inbox. Further failures update that notice without resetting
read state. A later scheduled run retries the sources.

A worker terminated without an exception handler is detected by the next trigger
or health check after its 330-second deadline. An unconfirmed handoff remains
queued for the next trigger. Expired undispatched work is flagged after ten
minutes. These checks require an active scheduler or independent health monitor;
no serverless process runs a permanent watchdog. Database outages cannot reliably
write an inbox notification, so enable the independent monitor's email alerts too.

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

MongoDB operations have a 15-second client deadline, including established
socket reads/writes and retries. Connection selection, connection establishment
and pool checkout are bounded to four seconds. A stalled workflow write cannot
hold refresh workers indefinitely. Cached workflow-service lookups bypass the
collector's initialization lock; configured database failures remain errors.
An interactive batch refresh skips projection when the history collector is busy;
the collector consumes the refreshed ledger and later attempts can retry projection.
Current queue charts do not wait for workflow backfill to finish.

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
2. Enable Fluid compute and set the Python function's maximum duration to **300
   seconds** in Vercel Project Settings. The Node dispatcher declares 300 seconds
   in its function configuration and bounds its worker connection to 255 seconds,
   reserving time to record failures and hand off. Both functions must be deployed.
   The Python legacy build remains; Node is an additional explicit build and route.
   `VERCEL_URL` supplies the trusted deployment origin. If Deployment Protection
   applies, configure Vercel's `VERCEL_AUTOMATION_BYPASS_SECRET` for internal calls.
3. Schedule `https://YOUR-PRODUCTION-HOST/api/data-refresh` every five minutes.
   Use POST with that authorization header. The daily Vercel job already specified
   in `vercel.json` runs at 19:00 UTC, or 00:30 India time the following day. It is
   a fallback, not the frequent capture schedule. Hobby can delay it within its
   scheduled hour and does not retry failed invocations automatically.
4. Create a **second cron-job.org job** for authorized `GET /api/data-health`,
   every five minutes, with the same Bearer header. Enable email notifications
   for failures and recovery in this monitor's Notifications settings, and enable
   failure notifications on the trigger job too. The monitor returns 503 for
   failed jobs or missing/overdue captures, including failures after a trigger
   already returned 202. Monitor independently of refresh requests.
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

The 8 October 2026 deployment test exceeded the standard cron-job.org timeout.
The stored daily source attempt took 206 seconds; a separate authenticated
`?sources=assignable_pool` test returned HTTP 200 with `refresh_ok: true` in
4.5 seconds. The original scan had not confirmed workflow completion. These are
observations from that deployment, not runtime guarantees. Splitting source
groups alone cannot make the measured daily capture fit a 30-second request.
The background dispatcher addresses the scheduler's short response timeout; it
does **not** remove Vercel's execution limit. If one source consistently exceeds
255 seconds, its job fails visibly and must be made resumable within that source
or moved to a longer-running worker. The observed 206-second daily scan fits this
budget, but a production test after redeployment is still required.

* [cron-job.org](https://cron-job.org/en/) supports free frequent jobs and custom
  authorization headers. Its [standard timeout is 30 seconds](https://cron-job.org/en/faq/).
  Use the quick trigger and independent health monitor described above. The
  30-second scheduler timeout covers enqueue/acknowledgment, not collection.
* `.github/workflows/capture-data.yml` is an opt-in alternative trigger. It accepts
  HTTP 202 on the new deployment. Set repository secrets `DASHBOARD_DEPLOYMENT_URL` and
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
| 202 on Vercel refresh | Job persisted and accepted; inspect health for completion |
| 200 on local synchronous refresh | Every requested source succeeded; inspect overall `ok` for other sources |
| 200 on health | All registered sources are recent and successful; latest job has no failures |
| 503 | Failed, absent, overdue source or persistence failure |
| 409 | Another collector owns the lease; retry after it completes or expires |
| 401 | Missing or incorrect secret (missing server configuration also fails closed) |
| 400 | Unknown or empty source selection |

Responses use `Cache-Control: no-store`. A health read never contacts the source
API or changes captures; it can reconcile an expired job and persist its failure
notification. Local Flask `/api/data-refresh` and the CLI retain synchronous
collection for operator use. The asynchronous trigger is a Vercel Node route.
For a server/terminal install, the same source registry is:

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

The local Python suite passes 351 tests and 26 subtests. Ten Node dispatcher tests
cover quick acknowledgment of a blocked worker, authenticated triggers, failed
enqueue, bounded connections, untrusted hosts, worker timeouts, duplicate claims,
invalid selections and handoff races. Recovery tests exercise source
failure isolation, retained success times, process restart, source expiry, deployment
version changes, concurrent jobs, old checkpoint rejection, storage failure,
endpoint authorization, targeted retries, raw-queue remapping and database reconnects.
Job tests additionally cover process restart, duplicate triggers, concurrent
workers, dead invocations, late results, remaining-source progress and persistent
notification read state. Both suites use isolated storage and synthetic sources.
An actual MongoDB wire-protocol fixture verifies an established workflow write
times out and a subsequent write recovers through the same client. A concurrency
regression checks cached workflow lookup while another thread owns the sync lock.
Pool inventory regressions cover the supplied zero response, normal plus urgent
hours, scope validation, missing/invalid metrics, expiry, expired sessions, forced
refresh sharing, user/date independence and removal of overview/funnel overrides.
Twelve browser checks cover dashboard, Daily Report and User Report at 390, 768,
1366 and 1920 pixels, including an unreconciled historical capture warning.
These checks use local fixtures; the production scheduler and scan duration still
need verification after deployment.
