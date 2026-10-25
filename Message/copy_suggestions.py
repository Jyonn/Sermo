from datetime import timedelta
from django.db.models import Count, Exists, OuterRef
from django.utils import timezone
from Message.models import Message, MessageCopy, MessageTypeChoice


def copyable_messages(chat, user):
    if not chat.group or chat.submission:
        return Message.objects.none()
    now = timezone.now()
    return Message.visible_for_user(chat, user).filter(
        type=MessageTypeChoice.TEXT, created_at__gte=now - timedelta(hours=24), created_at__lte=now,
    ).exclude(user=user).exclude(content='')


def copy_suggestions(chat, user):
    messages = copyable_messages(chat, user).annotate(
        copy_count=Count('copies'),
        copied=Exists(MessageCopy.objects.filter(message_id=OuterRef('pk'), user=user)),
    ).filter(copy_count__gt=0).select_related('user').order_by('-copy_count', '-created_at', '-id')[:3]
    return [dict(message_id=message.id, content=message.content, author=message.user.name,
                 created_at=message.created_at.timestamp(), copied=message.copied) for message in messages]


def record_copies(chat, user, message_ids):
    messages = list(copyable_messages(chat, user).filter(id__in=message_ids))
    MessageCopy.objects.bulk_create([MessageCopy(message=message, user=user) for message in messages], ignore_conflicts=True)
    return [message.id for message in messages]
