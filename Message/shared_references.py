"""Live, viewer-authorized references. Never snapshot research text into messages."""
import json
import re

KINDS = {15: 'profile', 16: 'research_task', 17: 'research_comment', 18: 'research_reply'}


def normalize(message_type, content):
    from Message.validators import MessageErrors
    try:
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError()
        key = 'profile_user_id' if message_type == 15 else 'project_id'
        if type(value.get(key)) is not int or value[key] <= 0:
            raise ValueError()
        result = dict(kind=KINDS[message_type], **{key: value[key]})
        for field in (('task_id',) if message_type == 16 else ('task_id', 'progress_id') if message_type in (17, 18) else ()):
            if not isinstance(value.get(field), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value[field]):
                raise ValueError()
            result[field] = value[field]
        return result
    except (ValueError, TypeError, KeyError):
        raise MessageErrors.PAYLOAD_INVALID


def payload(message_type, reference, viewer, *, require_access=False):
    from Message.validators import MessageErrors
    result = {key: reference[key] for key in ('profile_user_id', 'project_id', 'task_id', 'progress_id') if key in reference}
    result['kind'] = KINDS[message_type]

    def unavailable():
        if require_access:
            raise MessageErrors.PAYLOAD_INVALID
        return dict(result, unavailable=True)

    if viewer is None:
        return unavailable()
    if message_type == 15:
        from User.models import User
        person = User.objects.filter(pk=reference.get('profile_user_id'), space_id=viewer.space_id, is_deleted=False).first()
        if person is None:
            return unavailable()
        return dict(result, profile=person.dictify('user_id', 'name', 'signature', 'avatar_uri', 'avatar_cache_key', 'official', 'operator'))
    if not viewer.space.research_enabled:
        return unavailable()
    from Chat.models import ResearchProject, ChatMemberStatusChoice
    project = ResearchProject.objects.select_related('chat').filter(
        pk=reference.get('project_id'), chat__space_id=viewer.space_id, chat__is_deleted=False,
        chat__chat_members__user=viewer, chat__chat_members__status=ChatMemberStatusChoice.ACTIVE,
    ).first()
    if project is None:
        return unavailable()
    from Chat.research_status import normalize_tasks
    task = next((row for row in normalize_tasks(project.tasks) if row['id'] == reference.get('task_id')), None)
    if task is None:
        return unavailable()
    result.update(project_title=project.chat.title, title=task['title'], status=task['status'])
    if message_type == 16:
        result['text'] = task.get('body', '')[:240]
        return result
    progress = next((row for row in task.get('progress', []) if row['id'] == reference.get('progress_id')), None)
    if progress is None or progress.get('deleted_at') or bool(progress.get('reply_to')) != (message_type == 18):
        return unavailable()
    from User.models import User
    author = User.objects.filter(pk=progress['user_id'], space_id=viewer.space_id, is_deleted=False).first()
    result.update(text=progress.get('body', '')[:240], author_name=author.name if author else '', reply_to=progress.get('reply_to'))
    return result
