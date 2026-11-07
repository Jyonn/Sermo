"""One explicitly requested native delivery; no automatic production hook."""
import datetime

from django.utils import timezone

from User.native_push_delivery import NativePushDelivery as Delivery
from User.native_push_models import NativePushBinding as Binding
from User.native_push_payload import native_push_payload
from utils.apns import APNsUnavailable


def _content_allowed(event):
    from Chat.models import ChatMember, ChatMemberStatusChoice, ChatUserPreference
    from Message.models import Message
    from User.models import NotificationEventTypeChoice
    from User.native_push_targets import notification_target

    payload = event.payload if isinstance(event.payload, dict) else {}
    content_types = (
        NotificationEventTypeChoice.DIRECT_MESSAGE, NotificationEventTypeChoice.GROUP_MESSAGE,
        NotificationEventTypeChoice.SQUARE_STATEMENT_LIKE, NotificationEventTypeChoice.SQUARE_STATEMENT_COMMENT,
        NotificationEventTypeChoice.SQUARE_COMMENT_LIKE, NotificationEventTypeChoice.SQUARE_COMMENT_REPLY,
        NotificationEventTypeChoice.SQUARE_COMMENT_MENTION,
    )
    if event.event_type in content_types or payload.get('kind') in ('friend_statement', 'research_task', 'research_progress', 'research_update'):
        # A queued event is not lasting permission to disclose its content.
        if notification_target(event, event.user).get('kind') == 'unavailable':
            return False
    if event.event_type not in (NotificationEventTypeChoice.DIRECT_MESSAGE, NotificationEventTypeChoice.GROUP_MESSAGE):
        return True
    if any(type(payload.get(key)) is not int or payload[key] <= 0 for key in ('message_id', 'chat_id')):
        return False
    message = Message.objects.select_related('chat').filter(
        pk=payload['message_id'], chat_id=payload['chat_id'], chat__space_id=event.space_id,
        chat__is_deleted=False, is_deleted=False,
    ).first()
    if message is None or message.user_id == event.user_id:
        return False
    if not ChatMember.objects.filter(chat_id=message.chat_id, user=event.user, status=ChatMemberStatusChoice.ACTIVE).exists():
        return False
    if message.chat.group and ChatUserPreference.objects.filter(chat_id=message.chat_id, user=event.user, notifications_muted=True).exists():
        return False
    return message.is_visible_to(event.user)


def dispatch_native_delivery(delivery_id, transport):
    row = Delivery.claim(delivery_id, environments=transport.configuration.environments)
    if row is None:
        return None
    # Re-read consent after claim. No DB transaction is held across a network call.
    current = Delivery.objects.select_related('event__user', 'event__space', 'binding').filter(
        pk=row.pk, state='processing', claim_id=row.claim_id,
        claimed_at__gte=timezone.now() - datetime.timedelta(minutes=Delivery.CLAIM_MINUTES),
    ).first()
    if current is None:
        return None
    binding = Binding.active_for_user(current.event.user).filter(
        pk=current.binding_id, revision=current.binding_revision, token_digest=current.token_digest,
    ).first()
    if binding is None or not Delivery._event_allowed(current.event) or not _content_allowed(current.event):
        Delivery.finish(row.pk, row.claim_id, 'skipped')
        return 'skipped'
    try:
        payload = native_push_payload(current.event)
    except Exception:
        Delivery.finish(row.pk, row.claim_id, 'failed')
        return 'failed'
    try:
        result = transport.send(environment=binding.environment, token=binding.token, payload=payload,
                                notification_id=row.claim_id)
        outcome = {'accepted': 'sent', 'rejected': 'rejected', 'unknown': 'unknown'}.get(result.outcome, 'unknown')
    except (APNsUnavailable, ValueError):
        outcome, result = 'failed', None
    except Exception:
        # Unexpected exceptions after submission may conceal provider acceptance.
        outcome, result = 'unknown', None
    finished = Delivery.finish(row.pk, row.claim_id, outcome)
    if not finished:
        return 'unknown'
    timestamp = result.invalidated_at_ms if result is not None and outcome == 'rejected' else None
    if (finished and type(timestamp) is int and int(binding.last_seen_at.timestamp() * 1000) <= timestamp
            <= int(timezone.now().timestamp() * 1000) + 300000):
        Binding.invalidate_token(binding.pk, row.token_digest, row.binding_revision)
    return 'failed' if outcome == 'rejected' else outcome
