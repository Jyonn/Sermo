"""Resolve owned notifications to current, access-checked navigation identities."""
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views import View

from utils import auth


def _positive(value):
    return type(value) is int and 0 < value <= 2 ** 63 - 1


def _project_target(user, project, task_id=None, chat_message=None):
    if not user.space.chat_enabled or not user.space.research_enabled or not project or not project.chat.has_active_member(user):
        return dict(kind='unavailable')
    if task_id is not None:
        if (not isinstance(task_id, str) or not 1 <= len(task_id) <= 64 or not isinstance(project.tasks, list)
                or not any(isinstance(task, dict) and task.get('id') == task_id for task in project.tasks)):
            return dict(kind='unavailable')
    target = dict(kind='research', project_id=project.pk)
    if task_id is not None:
        target['task_id'] = task_id
    if chat_message is not None:
        target.update(kind='research_chat', chat_id=chat_message.chat_id, message_id=chat_message.pk)
    return target


def notification_target(event, user):
    from Chat.models import ChatPurposeChoice, ResearchProject, Submission
    from Message.models import Message
    from Square.models import Statement
    from User.models import NotificationEventTypeChoice as Kind

    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.event_type in (Kind.DIRECT_MESSAGE, Kind.GROUP_MESSAGE):
        if not _positive(payload.get('chat_id')) or not _positive(payload.get('message_id')):
            return dict(kind='unavailable')
        message = Message.objects.select_related('chat').filter(pk=payload['message_id'],
            chat_id=payload['chat_id'], chat__space_id=user.space_id, chat__is_deleted=False, is_deleted=False).first()
        if not message or not message.chat.has_active_member(user):
            return dict(kind='unavailable')
        if message.chat.submission and not Submission.objects.filter(chat=message.chat).exists():
            return dict(kind='unavailable')
        if not message.is_visible_to(user):
            return dict(kind='unavailable')
        if message.chat.purpose == ChatPurposeChoice.RESEARCH:
            project = ResearchProject.objects.select_related('chat').filter(chat=message.chat).first()
            return _project_target(user, project, chat_message=message)
        if message.chat.submission:
            if not user.space.submission_enabled:
                return dict(kind='unavailable')
            return dict(kind='submission', chat_id=message.chat_id, message_id=message.pk)
        if not user.space.chat_enabled:
            return dict(kind='unavailable')
        return dict(kind='chat', chat_id=message.chat_id, message_id=message.pk)
    if event.event_type in (Kind.SQUARE_STATEMENT_LIKE, Kind.SQUARE_STATEMENT_COMMENT,
                            Kind.SQUARE_COMMENT_LIKE, Kind.SQUARE_COMMENT_REPLY, Kind.SQUARE_COMMENT_MENTION) or payload.get('kind') == 'friend_statement':
        statement_id = payload.get('statement_id')
        if user.space.group_square_enabled and _positive(statement_id) and Statement.visible_for(user).filter(pk=statement_id).exists():
            return dict(kind='statement', statement_id=statement_id)
        return dict(kind='unavailable')
    if payload.get('kind') in ('research_task', 'research_progress', 'research_update'):
        project_id = payload.get('project_id')
        project = ResearchProject.objects.select_related('chat').filter(pk=project_id,
            chat__space_id=user.space_id, chat__is_deleted=False).first() if _positive(project_id) else None
        return _project_target(user, project, payload.get('task_id'))
    if payload.get('kind') in ('friend_request', 'friend_request_accepted', 'group_invite', 'group_invite_response'):
        return dict(kind='contacts')
    return dict(kind='unavailable')


class NativePushTargetView(View):
    @auth.require_user
    def get(self, request, event_id):
        from User.models import NotificationEvent

        # Foreign/missing/corrupt-space events share one response and reveal no IDs.
        event = NotificationEvent.objects.filter(pk=event_id, user=request.user, space_id=request.user.space_id).first() if _positive(event_id) else None
        if not event or request.GET:
            return JsonResponse(dict(identifier='NATIVE_PUSH@UNAVAILABLE', message=_('Notification unavailable'), body=None), status=404)
        return dict(notification_event_id=event.pk, account_id=request.user.pk,
                    space_slug=request.user.space.slug, target=notification_target(event, request.user))
