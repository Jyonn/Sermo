"""Shared visibility/filter policy for private history browsing and cleanup."""
import datetime

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from Message.models import Message, MessageEvent, MessageEventTypeChoice, MessageRetention, MessageUserState
from Message.validators import MessageErrors
from User.models import User


def matching_history(chat, user, *, keyword=None, types='', start=None, end=None, snapshot=None, retained=0):
    if not chat.has_active_member(user):
        raise MessageErrors.NOT_A_MEMBER
    query = Message.visible_for_user(chat, user)
    if keyword and keyword.strip():
        query = query.filter(content__icontains=keyword.strip())
    if types:
        try:
            values = [int(value) for value in types.split(',')]
            if not values or any(value not in range(14) for value in values):
                raise ValueError()
        except (TypeError, ValueError):
            raise MessageErrors.PAYLOAD_INVALID
        query = query.filter(type__in=values)
    if start is not None:
        query = query.filter(created_at__gte=datetime.datetime.fromtimestamp(start, datetime.timezone.utc))
    if end is not None:
        query = query.filter(created_at__lt=datetime.datetime.fromtimestamp(end, datetime.timezone.utc))
    if start is not None and end is not None and start >= end:
        raise MessageErrors.PAYLOAD_INVALID
    if snapshot is not None:
        query = query.filter(id__lte=snapshot)
    if retained:
        query = query.filter(retention_marks__user=user)
    return query


def read_history(chat, user, filters, before=None, request=None):
    query = matching_history(chat, user, **filters)
    snapshot = filters.get('snapshot')
    if snapshot is None:
        snapshot = query.aggregate(value=Max('id'))['value'] or 0
        query = query.filter(id__lte=snapshot)
    total = query.count()
    retained_count = query.filter(retention_marks__user=user).count()
    if before is not None:
        query = query.filter(id__lt=before)
    rows = list(query.select_related('user', 'reply_to', 'reply_to__user', 'media_resource__asset', 'forward_bundle')
                .prefetch_related('chat_mentions__user', 'forward_bundle__items__media_resource__asset').order_by('-id')[:101])
    more = len(rows) > 100
    rows = rows[:100]
    protected = set(MessageRetention.objects.filter(user=user, message_id__in=[row.id for row in rows]).values_list('message_id', flat=True))
    return dict(items=[dict(row.jsonl(request=request), retained=row.id in protected) for row in rows],
                total_count=total, retained_count=retained_count, snapshot_id=snapshot,
                has_more=more, next_before=rows[-1].id if more else None)


def manage_history(chat, user, filters, action, message_ids):
    # Serialize protection and cleanup for an account; visibility is always
    # rechecked on the server. Each request has bounded memory and transaction size.
    with transaction.atomic():
        User.objects.select_for_update().get(id=user.id)
        query = matching_history(chat, user, **filters)
        if message_ids:
            if len(set(message_ids)) != len(message_ids) or len(message_ids) > 500:
                raise MessageErrors.PAYLOAD_INVALID
            query = query.filter(id__in=message_ids)
            if query.count() != len(message_ids):
                raise MessageErrors.NOT_EXISTS
        if action in ('retain', 'release'):
            if not message_ids:
                raise MessageErrors.PAYLOAD_INVALID
            ids = list(query.values_list('id', flat=True))
            if action == 'retain':
                MessageRetention.objects.bulk_create([MessageRetention(user=user, message_id=mid) for mid in ids], ignore_conflicts=True)
            else:
                MessageRetention.objects.filter(user=user, message_id__in=ids).delete()
            return dict(changed_ids=ids, remaining=0)
        if filters.get('snapshot') is None:
            raise MessageErrors.PAYLOAD_INVALID
        deletable = query.exclude(retention_marks__user=user)
        if action == 'preview':
            return dict(count=query.count(), deletable_count=deletable.count(), retained_count=query.filter(retention_marks__user=user).count())
        if action != 'delete':
            raise MessageErrors.PAYLOAD_INVALID
        ids = list(deletable.order_by('id').values_list('id', flat=True)[:500])
        now = timezone.now()
        MessageUserState.objects.bulk_create([MessageUserState(message_id=mid, user=user, hidden_at=now) for mid in ids])
        MessageEvent.objects.bulk_create([MessageEvent(message_id=mid, chat=chat, actor=user, target_user=user,
                                                      type=MessageEventTypeChoice.HIDDEN, created_at=now) for mid in ids])
        return dict(changed_ids=ids, remaining=deletable.count())
