"""Research reporting policy, actionable requests and auditable project points.

All mutations run under ResearchView's project row lock and revision check.
Financial documents are never included in other students' project payloads.
"""
import copy
import json
import uuid
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone

KINDS = ('daily', 'weekly', 'monthly')
DEFAULT_REWARDS = [{'id': key, 'name': '', 'description': ''} for key in ('silver', 'gold', 'diamond')]


def today():
    return timezone.now().astimezone(ZoneInfo('Asia/Shanghai')).date()


def period_start(kind, day):
    return day if kind == 'daily' else day - timedelta(days=day.weekday()) if kind == 'weekly' else day.replace(day=1)


def state(project):
    value = copy.deepcopy(project.workflow or {})
    for key, default in [('milestones', []), ('reward_types', DEFAULT_REWARDS), ('ledger', []), ('reimbursements', [])]:
        value.setdefault(key, copy.deepcopy(default))
    return value


def reporting(body, rewards):
    value = body.get('reporting', {})
    if not isinstance(value, dict) or len(value) > 1 or any(key not in KINDS for key in value):
        raise ValueError('Invalid reporting schedule')
    for rule in value.values():
        if not isinstance(rule, dict) or type(rule.get('amount', 0)) is not int or not 0 <= rule.get('amount', 0) <= 10000:
            raise ValueError('Invalid report reward')
        if rule.get('reward_type', 'silver') not in {r['id'] for r in rewards}:
            raise ValueError('Unknown reward type')
    return value


def actionable(task, day):
    return task['status'] == 'active' and date.fromisoformat(task['start']) <= day and (not task.get('end') or day <= date.fromisoformat(task['end']))


def response_status(tasks):
    """A request is answered only by an actual reply from its designated recipient."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        replies = {}
        for entry in task.get('progress', []):
            if entry.get('reply_to') and entry.get('body', '').strip():
                replies.setdefault((entry['reply_to'], entry['user_id']), []).append(entry)
        for entry in task.get('progress', []):
            if not entry.get('response_to'):
                continue
            answers = replies.get((entry['id'], entry['response_to']), [])
            if answers:
                answer = min(answers, key=lambda row: row['created_at'])
                entry.update(resolved_at=answer['created_at'], resolved_by=answer['user_id'], resolved_reply_id=answer['id'])
            else:
                for key in ('resolved_at', 'resolved_by', 'resolved_reply_id'):
                    entry.pop(key, None)
    return tasks


def serialize(project, viewer):
    project.tasks = response_status(project.tasks)
    value = state(project)
    owner = project.chat.is_owner(viewer)
    sources = {p['id']: (task['id'], p) for task in project.tasks for p in task['progress']}
    for row in value['ledger']:
        source = sources.get(row.get('source_id'))
        if source:
            row['task_id'] = source[0]
        row['origin'] = row.get('origin', 'report' if source and source[1].get('report_kind') else 'manual')
        row['editable'] = owner and row['origin'] == 'manual'
    value['comment_awards'] = [{key: row[key] for key in ('id', 'user_id', 'reward_type', 'amount', 'reason', 'source_id', 'task_id', 'origin', 'created_at', 'editable') if key in row} for row in value['ledger'] if row.get('source_id') in sources and row['origin'] == 'manual']
    value['reimbursements'] = [r for r in value['reimbursements'] if owner or r['user_id'] == viewer.pk]
    value['ledger'] = [r for r in value['ledger'] if owner or r['user_id'] == viewer.pk]
    todos = []
    day = today()
    value['report_periods'] = {kind: period_start(kind, day).isoformat() for kind in KINDS}
    value['reportable_task_ids'] = [task['id'] for task in project.tasks if not owner and project.status == 'active' and viewer.pk in task['assignees'] and actionable(task, day)]
    if project.status == 'active':
        for task in project.tasks:
            if task['status'] != 'active':
                continue
            if not owner and viewer.pk in task['assignees'] and actionable(task, day):
                for kind in task.get('reporting', {}):
                    start = period_start(kind, day).isoformat()
                    if not any(p.get('report_kind') == kind and p['user_id'] == viewer.pk and p.get('period_start') == start for p in task['progress']):
                        todos.append(dict(id=task['id']+kind, task_id=task['id'], title=task['title'], kind=kind, period_start=start))
            for progress in task['progress']:
                if progress.get('response_to') == viewer.pk and not progress.get('resolved_at'):
                    todos.append(dict(id=progress['id'], task_id=task['id'], title=task['title'], kind='response', body=progress['body'], progress_id=progress['id']))
        if owner:
            todos += [dict(id=r['id'], title=r['title'], kind='reimbursement') for r in value['reimbursements'] if r['status'] == 'submitted']
        else:
            todos += [dict(id=r['id'], title=r['title'], kind='reimbursement') for r in value['reimbursements'] if r['status'] == 'needs_info']
    from Chat.research_reimbursements import CURRENCIES
    value['reimbursement_currencies'] = list(CURRENCIES)
    value['todos'] = todos
    return value


def grant(value, actor, user_id, reward_type, amount, reason, source, origin='manual'):
    if reward_type not in {r['id'] for r in value['reward_types']} or type(amount) is not int or not 1 <= amount <= 10000:
        raise ValueError('Invalid reward')
    if len(value['ledger']) >= 10000:
        raise ValueError('Reward record limit reached')
    value['ledger'].insert(0, dict(id=str(uuid.uuid4()), user_id=user_id, actor_id=actor.pk, reward_type=reward_type, amount=amount, reason=reason, source_id=source, origin=origin, created_at=timezone.now().isoformat()))


def progress_fields(project, task, actor, body, entry, active, owner):
    value = state(project)
    kind = body.get('report_kind')
    if kind:
        if kind not in KINDS or kind not in task.get('reporting', {}) or owner or actor.pk not in task['assignees'] or not actionable(task, today()):
            raise ValueError('Only assigned students can submit current reports')
        start = period_start(kind, today()).isoformat()
        if any(p['user_id'] == actor.pk and p.get('report_kind') == kind and p.get('period_start') == start for p in task['progress']):
            raise ValueError('A report for this period already exists')
        if type(body.get('waive_reward', False)) is not bool:
            raise ValueError('Invalid reward choice')
        entry.update(report_kind=kind, period_start=start, waive_reward=body.get('waive_reward', False))
        if kind == 'daily':
            slots = [body.get(key, 0) for key in ('ai_slots', 'thinking_slots')]
            if any(type(n) is not int or n < 0 or n > 48 for n in slots) or sum(slots) > 48:
                raise ValueError('Use half-hour increments within 24 hours')
            entry.update(ai_slots=slots[0], thinking_slots=slots[1])
        rule = task.get('reporting', {}).get(kind, {})
        if not entry['waive_reward'] and rule.get('amount', 0):
            grant(value, actor, actor.pk, rule.get('reward_type', 'silver'), rule['amount'], kind, entry['id'], origin='report')
    target = body.get('response_to')
    if target is not None:
        # Role enum values are resolved from membership, rather than trusted input.
        from Chat.models import ChatMemberRoleChoice, ChatMemberStatusChoice
        owner_id = project.chat.chat_members.filter(role=ChatMemberRoleChoice.OWNER, status=ChatMemberStatusChoice.ACTIVE).values_list('user_id', flat=True).first()
        if type(target) is not int or target not in active or target == actor.pk or (not owner and target != owner_id) or (owner and target == owner_id):
            raise ValueError('Choose the mentor or a student to answer')
        entry['response_to'] = target
    reply = next((p for p in task['progress'] if p['id'] == entry.get('reply_to')), None)
    if reply and reply.get('response_to') == actor.pk:
        reply['resolved_at'] = entry['created_at']
        reply['resolved_by'] = actor.pk
        reply['resolved_reply_id'] = entry['id']
    project.workflow = value


def mutate(project, actor, body, active, owner, clean_text):
    action = body['action']
    value = state(project)
    if action == 'milestone':
        identifier = body.get('id') or str(uuid.uuid4())
        rows = value['milestones']
        existing = next((r for r in rows if r['id'] == identifier), None)
        if body.get('id') and not existing:
            raise ValueError('Milestone unavailable')
        if body.get('delete'):
            rows.remove(existing)
        else:
            day = date.fromisoformat(body['date'])
            if day < project.start or (project.end and day > project.end) or (not existing and len(rows) >= 100):
                raise ValueError('Milestone must be within project dates')
            title = clean_text(body, 'title', 160)
            content = clean_text(body, 'body', 2000, False)
            row = dict(id=identifier, date=day.isoformat(), title=title, body=content)
            if existing:
                rows[rows.index(existing)] = row
            else:
                rows.append(row)
            rows.sort(key=lambda r: r['date'])
    elif action == 'reward_types':
        rows = body.get('reward_types')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
            raise ValueError('Choose 1–12 reward types')
        clean = []
        for row in rows:
            identifier = clean_text(row, 'id', 64)
            clean.append(dict(id=identifier, name=clean_text(row, 'name', 40, identifier not in ('silver', 'gold', 'diamond')), description=clean_text(row, 'description', 1000, False)))
        required = {r['reward_type'] for r in value['ledger']}
        required.update(rule.get('reward_type', 'silver') for t in project.tasks for rule in t.get('reporting', {}).values())
        if len({r['id'] for r in clean}) != len(clean) or not required.issubset({r['id'] for r in clean}):
            raise ValueError('Reward types in use must be retained')
        value['reward_types'] = clean
    elif action == 'award_edit':
        row = next((r for r in value['ledger'] if r['id'] == body.get('id')), None)
        source = next((p for task in project.tasks for p in task['progress'] if row and p['id'] == row.get('source_id')), None)
        if not row or row.get('origin', 'report' if source and source.get('report_kind') else 'manual') != 'manual':
            raise ValueError('Only manual awards can be edited')
        amount, reward_type = body.get('amount'), body.get('reward_type')
        if type(amount) is not int or not 1 <= amount <= 10000 or reward_type not in {r['id'] for r in value['reward_types']}:
            raise ValueError('Invalid reward')
        history = row.setdefault('history', [])
        if len(history) >= 100:
            raise ValueError('Award edit limit reached')
        reason = clean_text(body, 'reason', 1000)
        history.append({key: row.get(key) for key in ('amount', 'reward_type', 'reason', 'actor_id', 'updated_at')})
        row.update(amount=amount, reward_type=reward_type, reason=reason, actor_id=actor.pk, updated_at=timezone.now().isoformat(), origin='manual')
    elif action == 'award':
        if type(body.get('user_id')) is not int or body['user_id'] not in active:
            raise ValueError('Member unavailable')
        reason = clean_text(body, 'reason', 1000)
        source = clean_text(body, 'source_id', 64, False)
        if source and not any(p['id'] == source and p['user_id'] == body['user_id'] for t in project.tasks for p in t['progress']):
            raise ValueError('Comment unavailable')
        grant(value, actor, body['user_id'], body.get('reward_type'), body.get('amount'), reason, source)
    elif action == 'resolve_request':
        raise ValueError('Response requests are completed by replies, not manual actions')
    elif action == 'reimbursement':
        if len(value['reimbursements']) >= 1000:
            raise ValueError('Reimbursement limit reached')
        from Chat.research_reimbursements import create_reimbursement
        value['reimbursements'].insert(0, create_reimbursement(project, actor, body, clean_text))
    elif action == 'reimbursement_resubmit':
        row = next((r for r in value['reimbursements'] if r['id'] == body.get('id')), None)
        if not row or row['user_id'] != actor.pk or row['status'] != 'needs_info':
            raise ValueError('Reimbursement unavailable')
        supplement = clean_text(body, 'notes', 4000)
        if len(row.get('notes', '')) + len(supplement) > 12000:
            raise ValueError('Notes limit reached')
        row['notes'] = row.get('notes', '') + '\n\n' + supplement
        row['status'] = 'submitted'
        row['resubmitted_at'] = timezone.now().isoformat()
    elif action == 'reimbursement_review':
        row = next((r for r in value['reimbursements'] if r['id'] == body.get('id')), None)
        if not row or body.get('status') not in ('reviewed', 'needs_info', 'rejected'):
            raise ValueError('Reimbursement unavailable')
        row.update(status=body['status'], reviewer_id=actor.pk, reviewer_note=clean_text(body, 'notes', 2000, False), reviewed_at=timezone.now().isoformat())
    elif action == 'reimbursement_open':
        row = next((r for r in value['reimbursements'] if r['id'] == body.get('id')), None)
        if not row or (not owner and row['user_id'] != actor.pk) or body.get('resource_id') not in row['evidence']:
            raise ValueError('Document unavailable')
        from utils.qiniu import sign_private_download_url
        item = project.evidence.select_related('media__asset').get(reimbursement_id=row['id'], media_id=body['resource_id'])
        return {'url': sign_private_download_url(item.media.asset.source_uri)}
    else:
        raise ValueError('Unknown workflow action')
    if len(json.dumps(value)) > 2000000:
        raise ValueError('Workflow content limit reached')
    project.workflow = value
