# Batch workflow history and notifications: feasibility and implementation plan

Investigated 6 October 2026; implementation updated 7 October 2026 (Asia/Kolkata), in `E:/DEV/scorer`. Reporting Phase 2 remains the Individual User Report. The owner explicitly authorized persistent workflow records, synthetic missing approvals for completed batches, and notifications from the requests endpoint on 7 October.

## Accepted policy and implementation (7 October)

These instructions supersede the earlier verification-only gate for the history and inbox. They do not make unknown review dates or approved video hours available.

- A completed batch implies Admin approval and its Leader/Auditor prerequisites. Missing stages are persisted as **Synthetic**, with unknown actual review time and reviewer identity. Recording time is a separate field. Explicit source status transitions take priority; otherwise ordered observed batch approvals use Leader -> Auditor -> Admin with **Stage inferred** attribution. Unknown-time actions cannot establish approval ordering or return-cycle boundaries.
- Explicit returned batch review actions establish subsequent observed cycles. Do not invent unobserved submissions or cycles. An older completed ledger snapshot cannot complete a newer observed return cycle. Batch/task review logs and requests remain distinct evidence; notices and clip logs are not extra full-batch approval hours.
- A `sampling_pass_notice` is a green successful sampling notice; a `rework_notice` is an attention/rework notice with its reason and available `returned_by` identity. `request_pending` is upstream state, separate from local read/unread. A sampling notice alone does not assign an Admin stage or complete a batch.
- Existing notices are imported into the explicitly labeled **shared team inbox**, including available history. The earlier no-historical-notification bootstrap rule is superseded for this persistent inbox. There are no external messages/push notifications. The latest 10 are displayed; full storage and unread counts are retained. Opening the panel does not mark entries read. Clicking an entry saves its read state, closes the panel and opens batch/task-filtered history.
- Immutable sanitized source versions, batch observations and projection history preserve evidence. Synthetic entries become inactive/superseded when real matching stage evidence arrives. Policy and projection versions support later changes. Source disappearance does not delete notices/events. Separate read records prevent late decisions or role/name enrichment from resetting read state.
- `processing/workflow_history.py` provides MongoDB or PostgreSQL storage (a flexible `workflow_records` collection/table with indexed record kinds), with SQLite for local installations without a configured database. Configured storage failures propagate rather than silently switching persistence. Namespaced primary IDs enforce idempotency; persistent leases exclude concurrent collectors; projections reconcile after interruption. Source pages are durably bulk-written before checkpoint advancement.
- One bounded sync supplies both history and the inbox during application refreshes. Requests use `meta.total` and the actual returned `page_size`; review endpoints use cursors/progress guards. Operator identity comes from the authenticated session, not a hardcoded account or the pasted cookie. Successful batch-ledger refreshes also project new milestones without another source scan. Unattended capture was not added.
- `/workflow` provides storage-backed filtering/search and pagination, inclusive India date filters, observed versus synthetic evidence, actors, reasons, current batch state and repeated cycles. Unknown review times are displayed as Unknown; date filters use recorded dates for those rows. Deep links clear unrelated filters. Long reasons/cycles retain full tooltip text.

Live initialization created `slicing_dashboard.workflow_records` in the configured MongoDB using available authenticated records and the existing batch ledger. The first bounded pass retained 44 relevant notices from 46 requests, 3,113 active history actions including 1,865 synthetic prerequisites; the latest-10 inbox query worked against MongoDB. Batch/task review cursors remained partial and resume during later refreshes. Counts reflect that initial pass, not complete upstream history.

Validation and browser results are updated at the final checkpoint below. Local operational summaries and screenshots are ignored artifacts under `data/reports/`.

## Dashboard follow-up (7 October)

The approval area chart now consumes persisted dated batch approvals. Source status transitions determine stages where available; general approval order remains explicitly labeled as inferred. Durations come from batch video totals captured alongside evidence (or the retained batch ledger for older projections), so the UI labels approval hours as estimates. No task/clip logs, notification-only passes, or synthetic unknown-time milestones contribute daily hours. Duplicate batch/stage actions in a cycle count once; later return cycles remain distinct. Missing batch duration makes the affected stage/day unavailable rather than showing a partial total. A complete uncapped source scan supplies observed zero within retained review coverage; partial scans keep missing days as gaps. This is coverage of records visible to the configured account, not a guarantee of complete upstream workflow history.

Read-only validation found 632 dated Leader, 14 Auditor and 4 Admin approvals with retained batch duration. The current assigned/rework queues contained no tasks for `SSHD-S-Aditya3`; its historical assigned batch still retained 7,461 seconds despite 67 tasks pending review. The assigned plot now uses current task statuses instead of historical batch totals, with complete cursor pagination and a 60-second in-process capture lifetime. Failed reads are visible and cannot revive old ledger hours. Assignment dates remain Unknown when the task queue supplies none.

Validation: 218 tests and 27 subtests passed. Isolated desktop (1440px) and mobile (390px) Chrome checks verified all four area traces, themes, report navigation and no horizontal overflow. Browser artifacts are ignored under `data/reports/browser-area-chart/`. Application changes remain local on the testing branch.

## Settings and notification follow-up (7 October)

The dashboard now keeps only Slice Data Overview at the bottom. `/settings` holds Settlement periods and User mappings as two tabs with both editors mounted continuously. Switching tabs, pages, or themes preserves drafts and existing password/save validation. The dashboard no longer reads or renders Settlement Overview.

Assignment quantities remain authoritative live task-status totals. Dates are restored using original assignment timestamps when available, exact batch IDs, legacy return-history links, or an unambiguous shared date among the account's active ledger batches. The account fallback is labeled in chart hover. Date-only values retain their calendar day; timestamp values convert to India. Ages recalculate in India on each render, with age colors and separate date segments. Ambiguous account/batch dates remain Unknown. Read-only live validation recovered 1,177 of 1,201 assigned task dates and all 83 rework dates; Aditya3 had zero current assigned/rework tasks.

Notifications now include batch-review returns from any reviewer, observed Admin approvals, and inferred Admin completion milestones. Inferred completion retains unknown event time/reviewer, with its recorded time displayed separately. Admin notices use a stable batch/cycle identity so actual evidence enriches the existing notice and keeps read state. Matching request/review returns share the first durable notice ID, including when one source arrives late. Each return remains a separate event. Source sampling notices retain their existing success meaning.

The application polls workflow notifications every 60 seconds on all supported pages while open; bell clicks also request fresh source data. Initial history and old backfill do not replay as pop-ups. Recent newly detected unread actions show dismissible pop-ups with batch links. The persistent inbox keeps the latest ten, full unread count, compact references, reviewer/stage, relative time and individual/Mark all read actions. Pop-ups hide while the inbox is open. Each read-all action emits a distinct update so repeated actions immediately refresh counts. Read state remains shared across viewers.

Validation: 232 tests plus 27 subtests passed. Isolated Chrome checks at 1440px and 390px verified Settings unlock/edit retention, navigation, themes, assignment date/age hover data, Admin/return pop-ups, unobstructed inbox controls, physical read-all clicks and read persistence after reload, with no horizontal overflow. Read-only projection of existing evidence produced unique notification IDs. No production notification/read state was changed during UI QA. Changes remain local on `testing/reporting-phase-one`.

## Latest performance and refresh verification (7 October)

Repeated polls with identical source evidence and batch state skip projection/batch/notification writes. The fingerprint is committed in memory only after every projection write succeeds, so a partial failure is retried. New observed approvals or ledger changes still trigger reconciliation. A failed UI poll retains last known approval/inbox data while surfacing the error. Report loading, legend rendering and queue aggregates now run independently; hidden graphs resize when their page becomes visible. The bottom Slice Data Overview is removed. The owner approved a Daily Report day comparison and User Report activity calendar, both using retained submissions.

These changes are covered by cache/concurrency/coverage/failure retry regressions and desktop/mobile reload and legend tests. The read-only load benchmark intercepted all production report/snapshot persistence. Notification read state and workflow evidence were unchanged by UI QA and the benchmark.

## Historical feasibility assessment (6 October, retained notes)

An append-only event store, history UI, and persistent notifications are feasible. A **complete, accurately attributed batch lifecycle is not yet proven obtainable** with the current account and endpoints. Implement only verified event types after the verification gate below; preserve unresolved source observations separately. The Individual User Report can proceed using retained daily submissions. The Dashboard approval trend must wait for verified workflow events.

Evidence: source inspection, saved raw API captures from 5 October, and authenticated read-only endpoint checks on 6 October. No workflow actions, database writes, or notifications were performed. The existing HTTPScraper handled credentials/cookies. Local audit summary: `data/reports/workflow-feasibility-audit.json` (ignored operational artifact). The counts below describe samples and current account visibility, not global history completeness.

## CONFIRMED

- `scraper/http_scraper.py::HTTPScraper` authenticates with `/api/auth/login`, retains the session cookie in its httpx client, loads `/api/users`, and already consumes `/api/requests` and `/api/review/reviewed-tasks`. Reuse it without embedding credentials, operator IDs, or account IDs.
- `/api/users` returned 77 records in `items`, with ID, username, public ID, numeric role, capabilities, and group/leader relationships. Observed numeric roles were 5, 2, 8, and 3. The current account has role 5; a saved dashboard scope explicitly identifies this account as Leader. Other numeric role meanings and visibility of all reviewers remain unverified.
- `/api/requests` returned 30 records on page 1 and 2 on page 2 (`page_size=30`, `workflow_type=slice`, `meta.total=32`). Pagination exists, but this response has **no `has_more`**. Existing generic scraper pagination that defaults missing `has_more` to false can stop early. The daily-source request reader already has more careful pagination and should inform a dedicated workflow reader.
- Those 32 requests contained 26 `sampling_pass_notice`, 4 `rework_notice`, and 2 `task` records. The notices were `request_pending`; both `task` requests were `request_approved`. All 26 pass notices lacked `decided_by`; four returns had `returned_by`. Both `task` requests had `decided_by`. Request approval status alone therefore cannot mean Admin completion.
- Pass notices contain a batch token in `decision_note`; both `assignment_batch_id=` and `assigned_batch_id=` forms must be supported and validated. Direct batch IDs were present in all sampled batch review logs. Requests, tasks, and batch review logs are distinct entities.
- `/api/review/reviewed-batches` returned batch ID, unique-looking `review_log_id`, affected assignee, reviewer ID, action, decision, previous/current status, timestamp, and comment. The current sample had 30 records, total 1,031, `next_cursor`, `has_more`, and a 10,000-row offset/count limit. All sampled actions were `BATCH_REVIEW`; 28 moved to `batch_pending_auditor_review`, while two remained `batch_reviewing` despite `approved`. An approved decision alone does not prove a stage transition.
- Saved batch logs also contain a returned decision ending in `batch_rework`. Their reviewer IDs are populated but reviewer names are null. Names must be joined through an authoritative directory when possible.
- `/api/review/reviewed-tasks` returned task/clip IDs, `review_log_id`, reviewer ID, `reviewed_at`, action, decision, and previous/current status. The current sample has `CONFIRM_SLICE_LEADER`, `slice_submitted -> slice_pending_auditor_review`, and approved decisions. Saved samples additionally contain `SLICE_REWORK` and `REJECT_VIDEO_ERROR`. These are task/clip logs, not automatically independent batch events.
- Inventory exposes latest `slice_submitted_at` and current task status. Phase 1 preserves observed task/day submissions, not each submission attempt or batch action.
- `data_manager.py::sync_batch_returns` preserves return observations, but its source request uses `limit=100`. Existing `batches_master` represents latest batch state; neither dataset is a complete lifecycle event store.
- `processing/transitions.py` synthesizes submissions/approvals from current state, substitutes timestamps, collapses task/type pairs, and defaults unknown rework to Auditor. `processing/normalization.py` also uses actor-name heuristics. These existing analytics are unsuitable as authoritative audit/notification sources and must not be copied into the new ingestion path.
- Existing Dash shell has `/`, `/reports/daily`, and shared navigation/theme/refresh. Phase 2 adds `/reports/user`. Reusable table, section, Bootstrap badge/panel, date controls, and database adapters already exist.

## LIKELY, subject to verification

- `assignment_batch_id` is the strongest lifecycle correlation candidate: it is explicit on batch review logs and appears in request notes. Use `(source instance, workflow_type, assignment_batch_id)` if verified across return/resubmission. A batch contains many tasks; a request may mention only one representative task.
- A task's desensitized ID is an alternate task/session representation, not the batch identity. A request ID identifies a request object; it may expose more than one action as the request moves from creation to decision.
- Sampling pass notices probably reflect a sampling outcome, but the name, pending status, and worker requester do not establish who performed it. Correlating notices to reviewed-batch/task logs is a verification step, not an inference to persist as fact.
- The current account's scope likely explains why sampled review logs are dominated by Leader actions. Missing Auditor/Admin records may reflect permissions, different endpoints, retention, or sampling; absence is not evidence that no action occurred.

## UNKNOWN / NEEDS VERIFICATION

| Lifecycle step | Candidate evidence | Required before normalization |
| --- | --- | --- |
| User submission / resubmission | Task inventory latest timestamp; possible batch action log | Actual batch submit action, submitting actor, time, cycle identity, batch ID preserved on resubmit. A task assignment request is not proven to be submission. Latest timestamp polling can miss intervening cycles. |
| Leader pass | Explicit Leader task action plus batch review transition | Join actor role and task-to-batch membership at event time; verify one batch action versus many task/clip logs. Sample evidence supports Leader review but not all lifecycle paths. |
| Leader return | Saved returned batch review; `SLICE_REWORK` logs | Confirm role from authoritative actor/action semantics, link source identities and exact batch action. |
| Auditor pass | `sampling_pass_notice`; review logs | Trace the upstream action that creates the notice and its actor. Current pass notices do not supply an actor ID. Upstream server action implementation is not present in scorer; exact semantics remain unknown. |
| Auditor/Admin rework | `rework_notice`, `returned_by`, return history, review logs | Check authoritative role and all originating stages; do not treat `returned_by` or the request type as Auditor proof. |
| Admin completion | Batch completion transition or actual completion action log | Discover accessible endpoint with completion action, actor role, event timestamp, and stable batch ID. Latest completed status alone cannot establish an Admin-owned event. No verified Admin completion source in this audit. |

Still unverified: numeric role mapping beyond the observed Leader scope; IDs/names for every reviewer; historical role changes; `decided_by` semantics by request type; whether an assignment batch ID survives every resubmission; whether requests disappear after processing; API retention; full backfill coverage; exact timestamps/timezones of every source. The saved requests had 38 records and the current query 32, under potentially different filters/times; this is not proof of deletion or permanent retention.

## Verification gate and additional sources

1. Trace upstream request creation/decision and batch review actions in available upstream application source or authenticated UI/API traffic, including all four roles. Do not execute approvals/returns merely to produce evidence. Use existing examples or an explicitly designated test environment.
2. Obtain authoritative role enum/capability documentation and an authorized directory lookup for IDs absent from `/api/users`; snapshot role evidence at event time. The local canonical worker mapping is not an actor-role directory.
3. Follow at least one real batch through submission, Leader pass, Auditor pass/return, repeated resubmission, and Admin completion. Record source IDs, actors, timestamps and relationships. Confirm task membership and benchmark/clip exclusions.
4. Verify `/api/tasks/overview`, dashboard batch drilldowns or assignment-batch details for task-to-batch mapping; `/api/requests/batch-return-history` for returns; both review endpoints for explicit actions; an upstream submission/completion audit endpoint if existing sources omit these events. Exact new endpoint names must be discovered, not invented.
5. Check request pagination with `page/page_size/total`; review pagination with `next_cursor/has_more`, repeated-cursor guards and caps. The live sample used the authenticated session with `workflow_type=slice`; verify the supplied `operator_id` filter separately using the session/directory identity, without hardcoding the example ID. Probe authorized history to the actual oldest available record, noting permission/date filters and source retention. Do not claim full history based on `total` alone.

## Identity and normalized schema

Use separate raw observations, batch/task links, verified workflow events, sync checkpoints, and notification read state. Keep the current latest-state batch ledger separate.

`workflow_events`: immutable ID; source instance; workflow type; canonical batch ID; source batch ID; task ID(s); desensitized task ID; request ID; source review/action ID; submission cycle ID when provided; affected upstream user ID/name and canonical worker key; action/event type; result; actor ID/name; **one actor_type from User/Leader/Auditor/Admin**; original actor role and classification evidence; reason/comment; raw request type/status; previous/current status; created_at/decided_at; source event timestamp in UTC; source timezone; observed_at; source references; normalizer version; provenance/coverage; optional validated video-duration attribution.

Store only necessary raw payload fields and exclude credentials, cookies and unrelated user profile/contact data. Separate actor and affected worker. Resolve both upstream IDs and canonical worker aliases; preserve names/roles observed at the event for audit. A reliable role may be known even when actor ID/name is missing; retain null identity and role evidence instead of inventing a person.

Records with unknown actor category, ambiguous batch identity, action semantics, or event time remain in `workflow_observations` with resolution status/reason. They are not verified events, do not trigger notifications, and do not enter approval throughput. Do not add a fifth `Unknown` actor category to normalized events. Conflicting evidence also stays unresolved.

Batch/task membership needs temporal links: assignment batch, task ID/desensitized ID, validity interval/source, and cycle when available. Never correlate solely by worker + legacy batch number or by one task ID. If assignment batch identity changes during rework, retain both assignments and an explicit verified lifecycle link rather than conflating them.

## Deduplication and historical preservation

- One row per verified action. Return/resubmission cycles remain separate rows even when batch and actor repeat.
- Prefer namespaced immutable source action/review-log IDs. A request can represent creation **and** decision; key these separately (`request:<id>:created`, `request:<id>:decision:<stable decision identity>`). Do not use request ID alone to collapse those actions.
- For missing source IDs, use a deterministic hash of verified batch, action, source event timestamp, actor identity/role, cycle and source discriminator. If precision is insufficient to distinguish repeated actions, quarantine ambiguity rather than merge silently.
- Multiple task/clip logs may be evidence of one batch action. Prefer a batch action log; attach subordinate source references after proving correlation. Do not emit one duplicate batch event or full video duration per clip. Cross-endpoint aliases reference the same verified event; fuzzy same-time/name matching is insufficient.
- Unique indexes enforce idempotency under concurrent refreshes. Preserve immutable source observations and explicit enrichment/correction provenance; corrections must not erase earlier actions. Actor/name enrichment must not change event identity or create new notifications.

## Backfill and incremental synchronization

Use one workflow ingestion service with the existing authenticated client and application refresh coordinator. An interval/manual refresh requests a bounded sync; it does not launch separate API scans per chart, table or notification component. Refresh-driven collection is not unattended capture. A dedicated scheduled collector can be added later if operation between dashboard visits is required.

Initial backfill walks all **available and authorized** pages/cursors, inserts missing raw observations/events, checkpoints only after durable writes, and records coverage limits/errors. Full retrospective recovery is not promised. The accepted reporting policy still applies: begin preserving available evidence now while earlier periods remain incomplete.

Incremental sync uses source checkpoints plus an overlap window and revisits pending requests: an old request may gain a decision without a new creation ID. Periodic reconciliation detects delayed/out-of-order events; source disappearance does not delete stored events. Paginated records may move while scanning: deduplicate, validate progress and retry boundedly. Failed/partial pages do not advance a completed checkpoint. Persist error/retry state and use one sync lease to avoid multiple workers doing the same scan.

Store in MongoDB with unique indexes or PostgreSQL with unique constraints/upserts using existing adapters. Event insertion and notification projection must be atomic where possible or use a durable outbox/reconciliation cursor. Polling crashes between event insertion and projection must still produce the notification exactly once. Durable database storage is required for shared persistent notifications on serverless deployments; ephemeral files are not a sufficient substitute.

## Persistent notifications

Prefer notification records referencing immutable events, plus persistent read state. This supports indexed latest-10 queries without duplicating the complete event payload. Suggested fields: ID, unique workflow_event_id/type, recipient scope, type, title/message or template version, affected worker, actor/category, batch reference, source event time, created_at, is_read, read_at.

Current request-based triggers (7 October policy):

- `sampling_pass_notice` -> success notice, preserving source timestamp and current request state; this does not identify its approval stage.
- `rework_notice` -> rework notice with reason and available returner's identity.
- Synthetic prerequisites and other workflow actions remain in history and do not create additional duplicate notices.

Bootstrap: import available request notices into the shared persistent inbox, retaining older records while displaying the newest ten. The owner explicitly requested API notices as the inbox source. There are no external notification deliveries. A later external/push notification mechanism would need a separate enablement/baseline policy.

Enforce one notification per `(workflow_event_id, notification_type, recipient_scope)` with a unique index. Repeated ingestion, role enrichment, restarts and projection retries cannot duplicate it. Keep older notifications; the compact panel limit is presentation only.

Current scorer has no per-viewer authentication. The simplest initial recipient scope is an explicitly labeled **shared team inbox**; read/unread applies to everyone. Do not imply private per-user read state by using the worker dropdown. Reserve a recipient identity field for a later authenticated inbox.

Header bell -> latest 10 newest-first, ordered by event timestamp with ID tie-break; show full unread count across storage, not just the visible ten. Each entry shows member, batch, action/result, actor or known role, India timestamp (relative text plus exact time), and optional reason. Opening the panel does not mark everything read; clicking an entry persistently marks that entry read and opens its filtered batch history. Keep a visible read/unread distinction and accessible text labels.

Reuse Bootstrap semantic tokens/classes: success for completed, danger/warning for rework, primary/info for passed, secondary/info for submitted/pending. Use the same mapping in the event table. Color supplements textual labels and icons; no arbitrary new palette.

## History UI and lifecycle

Add `/workflow` using existing shell/navigation; keep notification control in the shared header. Reuse `report_section`, theme/date controls, and table styling, adding server-side filtering/pagination for growing history.

Table columns: India date/time, member, batch ID, task IDs (detail where lengthy), action, result, updated by, actor category, reason, workflow, source status. Filters: inclusive India date range, canonical member, batch, actor, actor category, action, result; quick Rework/Completed/Pending views. Search batch/task/desensitized IDs, member and actor names. â€œPendingâ€ here describes an event result; current outstanding work belongs in a separate current-state view.

Batch link opens a filtered table/detail panel (later direct `/workflow?batch=...&event=...`). Show all actions in chronological order, actor/category, exact time and reason, including repeated cycles. Show current batch state separately with its observation timestamp and any mismatch/coverage gap. Never draw missing approval steps as completed. This makes notification deep links useful without a separate detail route initially.

Retain raw rework reasons alongside optional later normalized reason categories. Later analytics may count returns per worker/reviewer, repeat-rework batches, stage turnaround and completion delay only where event pairs and denominators have sufficient coverage. Keep submitted video duration distinct from review count, clips and labor time. Never infer missing approval throughput from current state.

## Delivery sequence and files

These W phases are separate from reporting phases 1â€“3:

1. **W1 verification:** resolve endpoint semantics, roles, batch continuity, lifecycle/cycle identity and coverage. Save sanitized example fixtures and evidence matrix. Partial verified events may proceed only with explicit coverage; completion notifications remain gated on an Admin completion source.
2. **W2 model/storage:** add `processing/workflow_events.py`, `reporting/workflow_history.py`, event/observation/alias/checkpoint tables or collections in `db.py`, deterministic IDs and validation tests.
3. **W3 sync:** add `processing/workflow_source.py` using HTTPScraper, backfill command in `cli.py`, incremental coordinator in DataManager; outbox/checkpoint/lease recovery tests. Keep generic extraction/Excel behavior intact.
4. **W4 history UI:** `pages/workflow_history.py`, shared semantic badges, routing/callbacks in `app.py`, scoped CSS, filters/search/pagination and empty/error/coverage states.
5. **W5 notifications:** `reporting/notifications.py`, durable projection/read-state methods in `db.py`, shared header panel in `pages/components.py`, callbacks in `app.py`, latest-10/read/unread/dedup/bootstrap tests.
6. **W6 lifecycle:** batch detail/timeline and notification deep links, repeated-cycle and missing-stage presentation tests.
7. **W7 analytics:** verified stage throughput supports reporting Phase 3; rework analytics and supported user-report links follow. Do not replace the Phase 2 daily submission totals with review counts.

Required acceptance examples: four genuine actions -> four events; return/resubmit cycle example -> eight events with correct actors; repeated polling -> identical event/notification counts; missing reliable actor category -> unresolved observation; late request decision retained; clip logs do not multiply batch events; crash/retry still projects one notification; backfill creates no notification flood; reload preserves read state; latest-ten panel retains older records; unauthorized/unavailable sources show partial coverage; timezone boundaries and concurrent sync remain correct.

## Final implementation checkpoint (7 October)

207 tests plus 27 subtests passed. Coverage includes completed-batch synthesis, missing-middle enrichment/supersession, approval-order attribution, returns/repeated observed cycles, stale ledger handling, source timestamp boundaries, both batch-note forms and ambiguous links, total/page-size pagination, repeated-cursor rejection, indexed filtering/literal search/task aliases, source-scope isolation, atomic bulk page writes, leases, late decisions, polling idempotency, projection crash/retry, immutable projection versions, and durable inbox read state. The bounded `workflow-sync --backfill --max-rounds 5` CLI resumes available history; no unattended scheduler was introduced.

Offline fixture browser checks passed at 1440px and 390px for history rows, synthetic labels, latest ten entries, mark-read/deep-link/close behavior, reload persistence and theme switching. Both viewports avoided page-wide horizontal overflow; the full table scrolls horizontally. Screenshots are under ignored `data/reports/browser-workflow/`. In-app Browser bootstrap failed with an environment error; an isolated headless Chrome session provided these checks.

MongoDB schema initialization, source ingestion, bulk projections, latest-ten filtering and unread counting were exercised against the configured database. A second bounded live pass retained the same 44 notices, completed the accessible batch-review scan and expanded the active history to 3,932 records with 1,634 synthetic prerequisites; 231 synthetic prerequisites were superseded as source evidence arrived. Task-review history remained partial with a saved continuation cursor. PostgreSQL and SQLite adapters are provided; local regression tests exercise SQLite persistence, filtering and concurrency. No deployment or merge to main was performed.
