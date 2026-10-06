# Batch workflow history and notifications: feasibility and implementation plan

Investigated 6 October 2026, India time, in `E:/DEV/scorer`. This is a planned extension; reporting Phase 2 remains the Individual User Report. No workflow ingestion or notification code is authorized by this addition alone.

## Feasibility decision

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

Triggers after the verification gate:

- Admin-owned approval/completion action whose **verified resulting batch state is Completed** -> success notification. A generic request_approved or current completed status is insufficient.
- Verified return/rework action by Leader, Auditor or Admin -> rework notification, retaining the reason. Category comes from the normalized event, never the notice label.
- Submitted/pending/pass events remain in history initially; they do not generate notifications unless later enabled.

Bootstrap rule: persist `notifications_enabled_at` and complete an initial source baseline. Backfilled events are stored without notifications. Only live events occurring on/after enablement qualify; a late-discovered older event does not create a historical flood. Newer delayed events are eligible once resolved. No retrospective seeding by default.

Enforce one notification per `(workflow_event_id, notification_type, recipient_scope)` with a unique index. Repeated ingestion, role enrichment, restarts and projection retries cannot duplicate it. Keep older notifications; the compact panel limit is presentation only.

Current scorer has no per-viewer authentication. The simplest initial recipient scope is an explicitly labeled **shared team inbox**; read/unread applies to everyone. Do not imply private per-user read state by using the worker dropdown. Reserve a recipient identity field for a later authenticated inbox.

Header bell -> latest 10 newest-first, ordered by event timestamp with ID tie-break; show full unread count across storage, not just the visible ten. Each entry shows member, batch, action/result, actor or known role, India timestamp (relative text plus exact time), and optional reason. Opening the panel does not mark everything read; clicking an entry persistently marks that entry read and opens its filtered batch history. Keep a visible read/unread distinction and accessible text labels.

Reuse Bootstrap semantic tokens/classes: success for completed, danger/warning for rework, primary/info for passed, secondary/info for submitted/pending. Use the same mapping in the event table. Color supplements textual labels and icons; no arbitrary new palette.

## History UI and lifecycle

Add `/workflow` using existing shell/navigation; keep notification control in the shared header. Reuse `report_section`, theme/date controls, and table styling, adding server-side filtering/pagination for growing history.

Table columns: India date/time, member, batch ID, task IDs (detail where lengthy), action, result, updated by, actor category, reason, workflow, source status. Filters: inclusive India date range, canonical member, batch, actor, actor category, action, result; quick Rework/Completed/Pending views. Search batch/task/desensitized IDs, member and actor names. “Pending” here describes an event result; current outstanding work belongs in a separate current-state view.

Batch link opens a filtered table/detail panel (later direct `/workflow?batch=...&event=...`). Show all actions in chronological order, actor/category, exact time and reason, including repeated cycles. Show current batch state separately with its observation timestamp and any mismatch/coverage gap. Never draw missing approval steps as completed. This makes notification deep links useful without a separate detail route initially.

Retain raw rework reasons alongside optional later normalized reason categories. Later analytics may count returns per worker/reviewer, repeat-rework batches, stage turnaround and completion delay only where event pairs and denominators have sufficient coverage. Keep submitted video duration distinct from review count, clips and labor time. Never infer missing approval throughput from current state.

## Delivery sequence and files

These W phases are separate from reporting phases 1–3:

1. **W1 verification:** resolve endpoint semantics, roles, batch continuity, lifecycle/cycle identity and coverage. Save sanitized example fixtures and evidence matrix. Partial verified events may proceed only with explicit coverage; completion notifications remain gated on an Admin completion source.
2. **W2 model/storage:** add `processing/workflow_events.py`, `reporting/workflow_history.py`, event/observation/alias/checkpoint tables or collections in `db.py`, deterministic IDs and validation tests.
3. **W3 sync:** add `processing/workflow_source.py` using HTTPScraper, backfill command in `cli.py`, incremental coordinator in DataManager; outbox/checkpoint/lease recovery tests. Keep generic extraction/Excel behavior intact.
4. **W4 history UI:** `pages/workflow_history.py`, shared semantic badges, routing/callbacks in `app.py`, scoped CSS, filters/search/pagination and empty/error/coverage states.
5. **W5 notifications:** `reporting/notifications.py`, durable projection/read-state methods in `db.py`, shared header panel in `pages/components.py`, callbacks in `app.py`, latest-10/read/unread/dedup/bootstrap tests.
6. **W6 lifecycle:** batch detail/timeline and notification deep links, repeated-cycle and missing-stage presentation tests.
7. **W7 analytics:** verified stage throughput supports reporting Phase 3; rework analytics and supported user-report links follow. Do not replace the Phase 2 daily submission totals with review counts.

Required acceptance examples: four genuine actions -> four events; return/resubmit cycle example -> eight events with correct actors; repeated polling -> identical event/notification counts; missing reliable actor category -> unresolved observation; late request decision retained; clip logs do not multiply batch events; crash/retry still projects one notification; backfill creates no notification flood; reload preserves read state; latest-ten panel retains older records; unauthorized/unavailable sources show partial coverage; timezone boundaries and concurrent sync remain correct.
