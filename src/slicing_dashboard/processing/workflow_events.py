"""Project observed actions and explicitly marked business-rule milestones.

Completion implies Leader -> Auditor -> Admin under the owner's stated policy.
It proves prerequisites, not their dates, reviewer identities, or cycle count.
Source observations remain immutable; this projection can be rebuilt/enriched.
"""
from collections import defaultdict
from datetime import timezone
import math

from slicing_dashboard.processing.daily_work import instant

STAGES = ('Leader', 'Auditor', 'Admin')
STATUS_STAGE = {'batch_pending_auditor_review': 'Leader',
                'batch_pending_admin_review': 'Auditor', 'batch_completed': 'Admin'}


def timestamp(value):
    try:
        stamp = instant(value)
        return stamp.astimezone(timezone.utc).isoformat() if stamp else None
    except (TypeError, ValueError, OverflowError):
        return None


def indexed(record):
    record = dict(record)
    record.setdefault('projection_version', 1)
    record.setdefault('policy_version', 1)
    record['sort_at'] = record.get('event_at') or record['observed_at']
    stamp = instant(record['sort_at'])
    record['display_date'] = stamp.date().isoformat()
    record['search_text'] = ' '.join(str(record.get(key) or '') for key in
        ('member', 'batch_id', 'task_id', 'task_alias', 'actor', 'stage', 'action', 'reason', 'source_id')).casefold()
    return record


def project_events(observations, batches, previous, instance, observed_at):
    events, reviews = [], defaultdict(list)
    batch_map = {str(b['batch_id']): b for b in batches if b.get('batch_id')}
    previous_map = {row['id']: row for row in previous}
    for row in observations:
        if row.get('source_instance') != instance:
            continue
        raw = row['raw']
        # reviewed-batches also exposes the batch's current status separately
        # from the historical action's previous/current status.
        if row['source'] == 'batch-review' and row.get('batch_id') and raw.get('batch_status'):
            prior_batch = batch_map.get(row['batch_id'], {})
            if not prior_batch or (timestamp(prior_batch.get('last_updated')) or '') < row['observed_at']:
                batch_map[row['batch_id']] = {**prior_batch, 'batch_id': row['batch_id'],
                    'status': raw['batch_status'], 'canonical_user': row.get('member'),
                    'last_updated': row['observed_at']}
        event = {
            'id': row['event_key'], 'instance': instance, 'source_id': row.get('source_id'),
            'batch_id': row.get('batch_id'), 'task_id': row.get('task_id'),
            'task_alias': row.get('task_alias'), 'member': row.get('member'),
            'actor_id': row.get('actor_id'), 'actor': row.get('actor'),
            'stage': None, 'actor_type': None, 'cycle': 'initial',
            'action': row.get('action') or 'Unclassified action', 'result': row.get('result'),
            'reason': row.get('reason'), 'event_at': row.get('event_at'),
            'observed_at': previous_map.get(row['event_key'], {}).get('observed_at', row['observed_at']),
            'provenance': 'Observed', 'stage_basis': None, 'active': True,
            'source': row['source'], 'source_reference': row['id'], 'video_seconds': None,
            'source_status': raw.get('status') or raw.get('review_current_status'),
            'duration_basis': None, 'is_synthetic': False,
        }
        if row['source'] == 'request':
            if row['event_key'].endswith(':created'):
                event['action'] = {'sampling_pass_notice': 'Sampling Passed',
                                   'rework_notice': 'Returned for Rework'}.get(raw.get('request_type'), 'Request Created')
                event['result'] = {'sampling_pass_notice': 'Passed', 'rework_notice': 'Rework'}.get(raw.get('request_type'), raw.get('status'))
                # Requester is the affected worker; only returned_by names the
                # reviewer. A pass notice does not establish a particular role.
                if raw.get('request_type') != 'rework_notice':
                    event['actor_id'] = raw.get('decided_by')
                    event['actor'] = None
            else:
                event['action'] = 'Request Decision'
        elif row['source'] == 'batch-review' and event['batch_id']:
            reviews[event['batch_id']].append((row, event))
            continue
        elif row['source'] == 'task-review':
            event['actor_type'] = {'CONFIRM_SLICE_LEADER': 'Leader',
                                   'CONFIRM_SLICE_AUDITOR': 'Auditor',
                                   'CONFIRM_SLICE_ADMIN': 'Admin'}.get(raw.get('review_action'))
            # Clip/task records remain evidence; no batch duration attribution.
        events.append(event)

    current_cycles, cycle_returns = {}, {}
    for batch_id, rows in reviews.items():
        cycle, approvals = 'initial', 0
        for observation, event in sorted(rows, key=lambda pair: (pair[1].get('event_at') or '9999', pair[1]['id'])):
            raw = observation['raw']
            decision = str(raw.get('review_decision') or '').casefold()
            event['cycle'] = cycle
            if decision == 'approved':
                stage = STATUS_STAGE.get(raw.get('review_current_status'))
                if stage is None and event['event_at'] and approvals < 3:
                    stage = STAGES[approvals]
                    event['stage_basis'] = 'Approval order business rule'
                    event['provenance'] = 'Stage inferred'
                elif stage:
                    event['stage_basis'] = 'Source status transition'
                if stage:
                    event.update(stage=stage, actor_type=stage, action='Approved', result='Approved')
                    # The action time is observed; batch duration is a current
                    # snapshot, not a verified duration at the review instant.
                    duration = batch_map.get(batch_id, {}).get('total_duration_seconds')
                    try:
                        duration = float(duration)
                        if not math.isfinite(duration) or duration < 0:
                            duration = None
                    except (TypeError, ValueError):
                        duration = None
                    event['batch_video_seconds_at_capture'] = duration
                    approvals = max(approvals, STAGES.index(stage) + 1)
            elif decision in ('returned', 'rejected', 'rework') or raw.get('review_current_status') == 'batch_rework':
                reviewer_stage = {'batch_pending_leader_review': 'Leader', 'batch_reviewing': 'Leader',
                                  'batch_pending_auditor_review': 'Auditor',
                                  'batch_pending_admin_review': 'Admin'}.get(raw.get('review_previous_status'))
                event.update(action='Returned for Rework', result='Rework', actor_type=reviewer_stage,
                             stage=reviewer_stage, stage_basis='Source review queue' if reviewer_stage else None)
                # An unknown-time return cannot establish ordered cycle bounds.
                if event['event_at']:
                    cycle = 'after:' + event['id']
                    cycle_returns[cycle] = event['event_at']
                    approvals = 0
            events.append(event)
        current_cycles[batch_id] = cycle

    # The current ledger can prove stage prerequisites but cannot date them.
    # Fill earlier missing stages for observed passes as well as completion.
    milestones = defaultdict(list)
    for event in events:
        if event['source'] == 'batch-review' and event.get('stage') and event['action'] == 'Approved':
            milestones[(event['batch_id'], event['cycle'])].append(event)
    for batch_id, batch in batch_map.items():
        stage = STATUS_STAGE.get(batch.get('status'))
        if stage:
            cycle = current_cycles.get(batch_id, 'initial')
            # If the local ledger's observation predates a source return, its
            # completed status cannot establish completion in the new cycle.
            ledger_time = timestamp(batch.get('last_updated'))
            if cycle in cycle_returns and (not ledger_time or ledger_time < cycle_returns[cycle]):
                continue
            milestones[(batch_id, cycle)].append({
                'stage': stage, 'id': f'ledger:{batch_id}', 'member': batch.get('canonical_user') or batch.get('username'),
                'observed_at': observed_at, 'event_at': None,
            })
    for (batch_id, cycle), evidence in milestones.items():
        highest = max(STAGES.index(row['stage']) for row in evidence)
        for stage in STAGES[:highest + 1]:
            existing = [row for row in evidence if row['stage'] == stage and not row['id'].startswith('ledger:')]
            identifier = f'{instance}:synthetic:{batch_id}:{cycle}:{stage}'
            if existing:
                if identifier in previous_map:
                    events.append({**previous_map[identifier], 'active': False,
                                   'superseded_by': sorted(row['id'] for row in existing)})
                continue
            prior = previous_map.get(identifier, {})
            batch = batch_map.get(batch_id, {})
            duration = batch.get('total_duration_seconds')
            try:
                duration = float(duration)
                if not math.isfinite(duration) or duration < 0: duration = None
            except (TypeError, ValueError):
                duration = None
            events.append({
                'id': identifier, 'instance': instance, 'batch_id': batch_id, 'task_id': None,
                'member': evidence[-1].get('member'), 'stage': stage, 'actor_type': stage,
                'actor_id': None, 'actor': None, 'cycle': cycle, 'action': 'Approved', 'result': 'Approved',
                'event_at': None, 'observed_at': prior.get('observed_at', observed_at),
                'provenance': 'Synthetic', 'stage_basis': 'Completion/prerequisite business rule',
                'reason': 'Required prerequisite inferred from a later approval or completed batch. '
                          'Actual review time and reviewer identity are unavailable.',
                'is_synthetic': True, 'source': 'business-rule', 'active': True,
                'source_references': sorted(row['id'] for row in evidence),
                'video_seconds': None, 'duration_basis': None,
                'batch_video_seconds_at_capture': duration,
            })
    # Retain synthetic milestones that disappeared from the current projection
    # as inactive evidence, so subsequent polling never erases their provenance.
    identifiers = {row['id'] for row in events}
    for row in previous:
        if row.get('instance') == instance and row.get('is_synthetic') and row['id'] not in identifiers:
            events.append({**row, 'active': False})
    return [indexed(row) for row in events]


def project_notices(observations, instance, events=(), previous=()):
    notices = []
    previous_ids = {row['id'] for row in previous if row.get('instance') == instance}
    for row in observations:
        raw = row['raw']
        if (row.get('source_instance') != instance or row['source'] != 'request'
                or not row['event_key'].endswith(':created')
                or raw.get('request_type') not in ('sampling_pass_notice', 'rework_notice')):
            continue
        success = raw['request_type'] == 'sampling_pass_notice'
        notices.append(indexed({
            'id': row['event_key'] + ':notice', 'event_id': row['event_key'], 'instance': instance,
            'source_id': raw.get('id'), 'category': 'success' if success else 'warning',
            'title': 'Sampling Passed' if success else 'Rework Required',
            'action': 'Sampling Passed' if success else 'Returned for Rework',
            'member': row.get('member'), 'batch_id': row.get('batch_id'), 'task_id': row.get('task_id'),
            'task_alias': row.get('task_alias'), 'actor': raw.get('returned_by_name') if not success else None,
            'actor_id': raw.get('returned_by') if not success else raw.get('decided_by'),
            'stage': None, 'reason': raw.get('reason') or raw.get('decision_note') or '',
            'status': raw.get('status'), 'result': 'Passed' if success else 'Rework',
            'event_at': row.get('event_at'), 'observed_at': row['observed_at'],
            'provenance': 'Observed notice', 'active': True, 'recipient_scope': 'shared-team',
        }))
    # Review events work independently of the requests endpoint. Completion
    # milestones have one stable notice per batch/cycle, even when a later
    # observed Admin action enriches a previously inferred completion.
    for event in events:
        if event.get('instance') != instance or not event.get('active') or not event.get('batch_id'):
            continue
        returned = event.get('source') == 'batch-review' and event.get('action') == 'Returned for Rework'
        approved = event.get('stage') == 'Admin' and event.get('action') == 'Approved'
        if not returned and not approved:
            continue
        identifier = (event['id'] + ':notice' if returned else
                      f"{instance}:batch:{event['batch_id']}:{event.get('cycle', 'initial')}:admin-approved:notice")
        # Link matching request/review representations of one return without
        # creating a second notification or resetting the existing read ID.
        duplicate = None
        if returned and event.get('event_at'):
            for notice in notices:
                if (notice['action'] == 'Returned for Rework' and notice.get('batch_id') == event['batch_id']
                        and notice.get('notification_type') != 'batch_returned'
                        and notice.get('actor_id') is not None and notice.get('actor_id') == event.get('actor_id')
                        and notice.get('event_at') and abs((instant(notice['event_at']) - instant(event['event_at'])).total_seconds()) <= 60):
                    duplicate = notice
                    break
        inferred = bool(event.get('is_synthetic')) or event.get('provenance') == 'Stage inferred'
        title = ('Batch returned for rework' if returned else
                 'Batch completed · Admin approval inferred' if inferred else 'Batch approved by Admin')
        payload = indexed({
            # Preserve the first durable ID when the other source arrives
            # later, so enrichment cannot duplicate or reset a read notice.
            'id': (identifier if duplicate and identifier in previous_ids and duplicate['id'] not in previous_ids
                   else duplicate['id'] if duplicate else identifier),
            'instance': instance, 'event_id': event['id'],
            'source_id': event.get('source_id'), 'source': event.get('source'),
            'notification_type': 'batch_returned' if returned else 'batch_admin_approved',
            'category': 'warning' if returned else 'success', 'title': title,
            'action': event['action'], 'member': event.get('member'), 'batch_id': event['batch_id'],
            'task_id': event.get('task_id'), 'task_alias': event.get('task_alias'),
            'actor': event.get('actor'), 'actor_id': event.get('actor_id'), 'stage': event.get('actor_type') or event.get('stage'),
            'reason': event.get('reason') or ('Please check the batch and make the requested corrections.' if returned else 'The batch has completed Admin review.'),
            'status': event.get('source_status'), 'result': 'Rework' if returned else 'Approved',
            'event_at': event.get('event_at'), 'observed_at': event['observed_at'],
            'provenance': 'Completion inferred' if inferred else event.get('provenance', 'Observed'),
            'active': True, 'recipient_scope': 'shared-team',
        })
        if duplicate:
            duplicate.update(payload)
        else:
            existing = next((notice for notice in notices if notice['id'] == identifier), None)
            if existing is None:
                notices.append(payload)
            elif existing.get('provenance') == 'Completion inferred' and not inferred:
                existing.update(payload)
    return notices
