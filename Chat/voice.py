"""Foreground call policy and accounting. All mutations lock both global identities."""
import hashlib
import hmac
import json
import math
import uuid
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatPurposeChoice
from Chat.voice_models import VoiceCall, VoiceIdentity, VoiceWeeklyQuota

ACTIVE = ('ringing', 'connecting', 'active')
ZONE = ZoneInfo('Asia/Shanghai')
WEEK_SECONDS = 300


class CallError(Exception):
    def __init__(self, reason, status=409):
        self.reason, self.status = reason, status


def enabled():
    return bool(getattr(settings, 'VOICE_CALLS_ENABLED', False))


def require_enabled():
    if not enabled():
        raise CallError('unavailable', 503)


def week_at(now):
    day = now.astimezone(ZONE).date()
    return day - timedelta(days=day.weekday())


def week_end(week):
    return datetime.combine(week + timedelta(days=7), time.min, tzinfo=ZONE)


def identity_key(user):
    if not user.phone or not user.phone_verified_at:
        raise CallError('phone_required')
    # Verified phone values are normalized by the existing phone binding flow.
    return hmac.new(settings.SECRET_KEY.encode(), str(user.phone).encode(), hashlib.sha256).hexdigest()


def participants(chat, user):
    if chat.is_deleted or not chat.direct or chat.purpose != ChatPurposeChoice.NORMAL or not chat.has_active_member(user):
        raise CallError('forbidden', 403)
    chat.space.require_chat_enabled()
    members = list(ChatMember.objects.filter(chat=chat, status=ChatMemberStatusChoice.ACTIVE, user__is_deleted=False).select_related('user'))
    if len(members) != 2:
        raise CallError('forbidden', 403)
    peer = next((m.user for m in members if m.user_id != user.id), None)
    if peer is None:
        raise CallError('forbidden', 403)
    for member in (user, peer):
        chat.require_user_message_allowed(member)
    return peer


def remaining(key, week):
    row = VoiceWeeklyQuota.objects.filter(identity_id=key, week=week).first()
    return max(0, WEEK_SECONDS - row.used_seconds - row.reserved_seconds) if row else WEEK_SECONDS


@contextmanager
def lock_identities(keys):
    # Creation order and lock order must agree for calls across different spaces.
    with transaction.atomic():
        for key in sorted(set(keys)):
            VoiceIdentity.objects.get_or_create(digest=key)
        rows = list(VoiceIdentity.objects.select_for_update().filter(digest__in=keys).order_by('digest'))
        yield {row.digest: row for row in rows}


def capability(chat, user):
    peer = participants(chat, user)
    now = timezone.now()
    result = dict(enabled=enabled(), can_call=False, reason='', remaining_seconds=0, max_seconds=0,
                  reset_at=week_end(week_at(now)).timestamp(), peer=peer.tiny_json(), chat_id=chat.id)
    try:
        require_enabled()
        from Chat.voice_media import configured
        if not configured():
            raise CallError('unavailable', 503)
        if not user.is_permanent_vip or not peer.is_permanent_vip:
            raise CallError('vip_required')
        own, other = identity_key(user), identity_key(peer)
        if own == other:
            raise CallError('same_person')
        if VoiceIdentity.objects.filter(digest__in=[own, other], active_call__state__in=ACTIVE).exists():
            raise CallError('busy')
        result['remaining_seconds'] = remaining(own, week_at(now))
        if result['remaining_seconds'] <= 0:
            raise CallError('quota_empty')
        if remaining(other, week_at(now)) <= 0:
            raise CallError('peer_quota_empty')
        maximum = min(result['remaining_seconds'], remaining(other, week_at(now)),
                      max(0, math.floor((week_end(week_at(now)) - now).total_seconds())))
        if maximum <= 0:
            raise CallError('unavailable')
        result.update(can_call=True, max_seconds=maximum)
    except CallError as exc:
        result['reason'] = exc.reason
    return result


def payload(call):
    return dict(kind='voice_call', call_id=str(call.id), chat_id=call.chat_id, state=call.state, reason=call.reason,
                duration_seconds=call.duration_seconds, max_seconds=call.max_seconds,
                caller_id=call.caller_id, callee_id=call.callee_id,
                connected_at=call.connected_at.timestamp() if call.connected_at else None,
                deadline_at=call.deadline_at.timestamp() if call.deadline_at else None)


def owns(call, user, device):
    return str(call.caller_device if user.id == call.caller_id else call.callee_device) == str(device)


def serialize(call, user, device=None):
    peer = call.callee if user.id == call.caller_id else call.caller
    return dict(**payload(call), peer=peer.tiny_json(), owned=owns(call, user, device), expires_at=call.expires_at.timestamp(),
                server_now=timezone.now().timestamp(), message_id=call.message_id)


def update_message(call, created=False):
    from Message.models import Message, MessageEvent, MessageEventTypeChoice, MessageTypeChoice
    content = json.dumps(payload(call), separators=(',', ':'))
    if created:
        message = Message.objects.create(chat=call.chat, user=call.caller, type=MessageTypeChoice.VOICE_CALL,
                                         content=content, client_message_id=f'voice:{call.id}')
        call.message = message
        call.save(update_fields=['message'])
        MessageEvent.record_created(message)
        Chat.objects.filter(pk=call.chat_id).update(last_chat_at=timezone.now())
    elif call.message_id:
        Message.objects.filter(pk=call.message_id).update(content=content)
        MessageEvent.objects.create(message_id=call.message_id, chat_id=call.chat_id, actor=call.caller,
                                    type=MessageEventTypeChoice.UPDATED)


def create(chat, user, client_key, device):
    require_enabled()
    peer = participants(chat, user)
    try:
        client_key = uuid.UUID(str(client_key))
        device = uuid.UUID(str(device))
    except (ValueError, TypeError, AttributeError):
        raise CallError('invalid_request', 400)
    keys = [identity_key(user), identity_key(peer)]
    with lock_identities(keys) as identities:
        existing = VoiceCall.objects.filter(caller=user, client_key=client_key).first()
        if existing:
            if existing.chat_id != chat.id:
                raise CallError('invalid_request', 400)
            return existing
        cap = capability(chat, user)
        if not cap['can_call']:
            raise CallError(cap['reason'])
        now = timezone.now()
        call = VoiceCall.objects.create(chat=chat, caller=user, callee=peer, caller_identity_id=keys[0],
            callee_identity_id=keys[1], client_key=client_key, week=week_at(now), max_seconds=cap['max_seconds'],
            created_at=now, caller_seen_at=now, caller_device=device, expires_at=min(now+timedelta(seconds=30), week_end(week_at(now))))
        for key in keys:
            quota, _ = VoiceWeeklyQuota.objects.get_or_create(identity_id=key, week=call.week)
            quota.reserved_seconds += call.max_seconds
            quota.save(update_fields=['reserved_seconds'])
            identities[key].active_call = call
            identities[key].save(update_fields=['active_call'])
        update_message(call, created=True)
        from User.models import NotificationEvent, NotificationEventTypeChoice
        event = NotificationEvent.objects.create(space=peer.space, user=peer, actor=user,
            event_type=NotificationEventTypeChoice.VOICE_CALL,
            payload=dict(kind='voice_call', call_id=str(call.id), chat_id=chat.id, message_id=call.message_id,
                         message_type=14, content='Call', expires_at=call.expires_at.timestamp()))
        if getattr(settings, 'VOICE_CALL_NOTIFICATIONS_ENABLED', False):
            NotificationEvent._enqueue_deliveries_after_commit([event.id])
        return call


def _end_locked(call, reason, now):
    if call.state == 'ended':
        return
    call.duration_seconds = min(call.max_seconds, max(0, math.ceil((min(now, call.deadline_at)-call.connected_at).total_seconds()))) if call.connected_at else 0
    call.state, call.reason, call.ended_at = 'ended', reason, now
    call.save(update_fields=['duration_seconds', 'state', 'reason', 'ended_at'])
    for key in sorted([call.caller_identity_id, call.callee_identity_id]):
        quota = VoiceWeeklyQuota.objects.select_for_update().get(identity_id=key, week=call.week)
        quota.reserved_seconds = max(0, quota.reserved_seconds-call.max_seconds)
        quota.used_seconds = min(WEEK_SECONDS, quota.used_seconds+call.duration_seconds)
        quota.save(update_fields=['reserved_seconds', 'used_seconds'])
        VoiceIdentity.objects.filter(digest=key, active_call=call).update(active_call=None)
    update_message(call)
    # Durable cleanup is retried by the watchdog, outside this transaction.


def expire_locked(call, now):
    if call.state == 'ended':
        return
    if call.connected_at and now >= call.deadline_at:
        _end_locked(call, 'quota', call.deadline_at)
    elif call.state in ('ringing', 'connecting') and now >= call.expires_at:
        _end_locked(call, 'missed' if call.state == 'ringing' else 'failed', now)
    elif call.state in ('connecting', 'active') and any(value is None or now-value > timedelta(seconds=10) for value in [call.caller_seen_at, call.callee_seen_at]):
        _end_locked(call, 'disconnected', now)
    elif call.state == 'ringing' and now-call.caller_seen_at > timedelta(seconds=10):
        _end_locked(call, 'cancelled', now)


@contextmanager
def locked_call(call_id, user=None):
    try:
        call_id = uuid.UUID(str(call_id))
        call = VoiceCall.objects.get(pk=call_id)
    except (VoiceCall.DoesNotExist, ValueError, TypeError, AttributeError):
        raise CallError('not_found', 404)
    if user is not None and user.id not in (call.caller_id, call.callee_id):
        raise CallError('forbidden', 403)
    with lock_identities([call.caller_identity_id, call.callee_identity_id]):
        call = VoiceCall.objects.select_for_update().select_related('caller', 'callee', 'chat').get(pk=call.id)
        yield call


def mutate(call_id, user, action, device):
    try:
        device = uuid.UUID(str(device))
    except (ValueError, TypeError, AttributeError):
        raise CallError('invalid_request', 400)
    with locked_call(call_id, user) as call:
        now = timezone.now()
        expire_locked(call, now)
        if action != 'accept' and call.state != 'ended' and not owns(call, user, device):
            # Any signed-in callee may decline an unanswered invitation.
            if not (action == 'end' and call.state == 'ringing' and user.id == call.callee_id):
                raise CallError('other_device')
        if action == 'end':
            _end_locked(call, 'completed' if call.connected_at else 'cancelled' if user.id == call.caller_id else 'declined', now)
        elif call.state != 'ended':
            if action == 'accept':
                if user.id != call.callee_id:
                    raise CallError('forbidden', 403)
                if call.callee_device and call.callee_device != device:
                    raise CallError('other_device')
                if call.state == 'ringing':
                    participants(call.chat, user)
                    unchanged = all(member.phone and member.phone_verified_at and identity_key(member)==key for member,key in
                                    [(call.caller,call.caller_identity_id),(call.callee,call.callee_identity_id)])
                    if not unchanged or not call.caller.is_permanent_vip or not call.callee.is_permanent_vip:
                        _end_locked(call, 'ineligible', now)
                    else:
                        call.state, call.accepted_at, call.callee_seen_at = 'connecting', now, now
                        call.callee_device = device
                        call.expires_at = min(now+timedelta(seconds=15), week_end(call.week))
                        call.save(update_fields=['state', 'accepted_at', 'callee_seen_at', 'callee_device', 'expires_at'])
                        update_message(call)
            elif action == 'heartbeat':
                field = 'caller_seen_at' if user.id == call.caller_id else 'callee_seen_at'
                setattr(call, field, now)
                call.save(update_fields=[field])
            else:
                raise CallError('invalid_request', 400)
        return call


def current(user):
    call = VoiceCall.objects.filter(Q(caller=user)|Q(callee=user), state__in=ACTIVE).order_by('-created_at').first()
    if not call:
        return None
    with locked_call(call.id, user) as call:
        expire_locked(call, timezone.now())
        return call


def reconcile(limit=100):
    """A durable per-second worker owns timeout and media cleanup, not web clients."""
    from Chat.voice_media import close_room
    ids = list(VoiceCall.objects.filter(state__in=ACTIVE).order_by('created_at').values_list('id', flat=True)[:limit])
    for call_id in ids:
        with locked_call(call_id) as call:
            expire_locked(call, timezone.now())
    for call in VoiceCall.objects.filter(state='ended', media_closed_at__isnull=True).order_by('ended_at')[:limit]:
        if close_room(call):
            VoiceCall.objects.filter(pk=call.pk, state='ended').update(media_closed_at=timezone.now())
