"""Group voice rooms and per-participant accounting."""
import math
import uuid
from datetime import timedelta

import requests
from django.db import transaction
from django.utils import timezone

from Chat import voice, voice_media
from Chat.models import Chat, ChatPurposeChoice
from Chat.voice_models import GroupVoiceCall, GroupVoiceParticipant, VoiceWeeklyQuota


ROOM_PREFIX = 'frienden-group-'
HEARTBEAT_TIMEOUT = timedelta(seconds=10)
CONNECT_TIMEOUT = timedelta(seconds=15)


def check_member(chat, user):
    if (chat.is_deleted or not chat.group or chat.purpose != ChatPurposeChoice.NORMAL
            or user.is_deleted or not chat.has_active_member(user) or not chat.space.chat_enabled
            or chat.send_restriction_for(user)['active']):
        raise voice.CallError('forbidden', 403)
    if not user.is_permanent_vip:
        raise voice.CallError('vip_required')
    return voice.identity_key(user)


def eligibility(chat, user, now):
    voice.require_enabled()
    if not voice_media.configured():
        raise voice.CallError('unavailable', 503)
    key = check_member(chat, user)
    week = voice.week_at(now)
    if voice.identities_busy([key]):
        raise voice.CallError('busy')
    remaining = voice.remaining(key, week)
    maximum = min(remaining, math.floor((voice.week_end(week) - now).total_seconds()))
    if maximum <= 0:
        raise voice.CallError('quota_empty')
    return key, remaining, maximum


def capability(chat, user):
    now = timezone.now()
    result = dict(group=True, enabled=voice.enabled(), can_call=False, reason='', remaining_seconds=0,
                  max_seconds=0, reset_at=voice.week_end(voice.week_at(now)).timestamp(),
                  peer=user.tiny_json(), chat_id=chat.id, ongoing=False)
    try:
        _, remaining, maximum = eligibility(chat, user, now)
        result.update(can_call=True, remaining_seconds=remaining, max_seconds=maximum,
                      ongoing=GroupVoiceCall.objects.filter(chat=chat, ended_at__isnull=True).exists())
    except voice.CallError as exc:
        result['reason'] = exc.reason
    return result


def record(call):
    duration = math.ceil((call.ended_at - call.connected_at).total_seconds()) if call.ended_at and call.connected_at else 0
    return dict(kind='voice_call', group=True, call_id=str(call.id), chat_id=call.chat_id,
                caller_id=call.caller_id, callee_id=None, state='ended' if call.ended_at else 'active',
                reason='completed' if call.ended_at else '', duration_seconds=duration, max_seconds=0,
                connected_at=call.connected_at.timestamp() if call.connected_at else None, deadline_at=None)


def serialize(seat, device):
    call = seat.call
    result = record(call)
    result.update(state='ended' if seat.left_at else 'active' if seat.joined_at else 'connecting',
                  duration_seconds=seat.duration_seconds, max_seconds=seat.reserved_seconds,
                  connected_at=seat.joined_at.timestamp() if seat.joined_at else None,
                  deadline_at=seat.expires_at.timestamp() if seat.joined_at else None,
                  expires_at=seat.expires_at.timestamp(), server_now=timezone.now().timestamp(),
                  owned=str(seat.device) == str(device), message_id=call.message_id,
                  peer=call.caller.tiny_json(), group_name=call.chat.title,
                  participants=[member.user.tiny_json() for member in call.participants.filter(
                      left_at__isnull=True, joined_at__isnull=False).select_related('user').order_by('joined_at')])
    return result


def leave(seat, now):
    if seat.left_at:
        return
    call = seat.call
    with voice.lock_identities([seat.identity_id]):
        quota = VoiceWeeklyQuota.objects.get(identity_id=seat.identity_id, week=seat.week)
        if seat.joined_at:
            seat.duration_seconds = min(seat.reserved_seconds, max(0, math.ceil(
                (min(now, seat.expires_at) - seat.joined_at).total_seconds())))
        seat.left_at = now
        seat.save(update_fields=['duration_seconds', 'left_at'])
        quota.reserved_seconds -= seat.reserved_seconds
        quota.used_seconds += seat.duration_seconds
        quota.save(update_fields=['reserved_seconds', 'used_seconds'])
    if not call.participants.filter(left_at__isnull=True).exists():
        call.ended_at = now
        call.save(update_fields=['ended_at'])
        voice.update_message(call, record=record(call))


def expire(call, now):
    seats = call.participants.filter(left_at__isnull=True).select_related('user').order_by('identity_id')
    # Acquire all potentially released identities in the same order as direct calls.
    seats = list(seats)
    with voice.lock_identities([seat.identity_id for seat in seats]):
        for seat in seats:
            try:
                allowed = check_member(call.chat, seat.user) == seat.identity_id
            except voice.CallError:
                allowed = False
            heartbeat_deadline = seat.last_seen_at + HEARTBEAT_TIMEOUT
            if not allowed or now >= min(seat.expires_at, heartbeat_deadline):
                leave(seat, min(now, heartbeat_deadline))


def join(chat, user, client_key, device):
    voice.require_enabled()
    client_key, device = uuid.UUID(str(client_key)), uuid.UUID(str(device))
    with transaction.atomic():
        chat = Chat.objects.select_for_update().get(pk=chat.pk)
        check_member(chat, user)
        existing = GroupVoiceParticipant.objects.filter(user=user, client_key=client_key).first()
        if existing:
            if existing.call.chat_id != chat.id or existing.device != device:
                raise voice.CallError('invalid_request', 400)
            return existing
        call = GroupVoiceCall.objects.select_for_update().filter(chat=chat, ended_at__isnull=True).first()
        now = timezone.now()
        if call:
            expire(call, now)
    with transaction.atomic():
        # Expiration above commits even if the request cannot join.
        chat = Chat.objects.select_for_update().get(pk=chat.pk)
        key = check_member(chat, user)
        call = GroupVoiceCall.objects.select_for_update().filter(chat=chat, ended_at__isnull=True).first()
        with voice.lock_identities([key]):
            existing = GroupVoiceParticipant.objects.filter(user=user, client_key=client_key).first()
            if existing:
                if existing.call.chat_id != chat.id or existing.device != device:
                    raise voice.CallError('invalid_request', 400)
                return existing
            now = timezone.now()
            _, _, maximum = eligibility(chat, user, now)
            week = voice.week_at(now)
            quota, _ = VoiceWeeklyQuota.objects.get_or_create(identity_id=key, week=week)
            quota.reserved_seconds += maximum
            quota.save(update_fields=['reserved_seconds'])
            if not call:
                call = GroupVoiceCall.objects.create(chat=chat, caller=user, created_at=now)
                voice.update_message(call, created=True, record=record(call))
            return GroupVoiceParticipant.objects.create(call=call, user=user, identity_id=key,
                device=device, client_key=client_key, week=week, reserved_seconds=maximum,
                last_seen_at=now, expires_at=min(now + CONNECT_TIMEOUT, voice.week_end(week)))


def action(call_id, user, name, device):
    device = uuid.UUID(str(device))
    with transaction.atomic():
        call = GroupVoiceCall.objects.select_for_update().get(pk=call_id)
        seat = call.participants.filter(user=user, device=device).order_by('-last_seen_at').first()
        if not seat:
            raise voice.CallError('other_device', 403)
        now = timezone.now()
        expire(call, now)
        seat.refresh_from_db()
        if name == 'media':
            if not seat.left_at and voice_media.configured():
                return dict(url=voice.settings.VOICE_LIVEKIT_URL, token=voice_media.token(call, user, seat.id))
        elif name == 'end':
            leave(seat, now)
        elif name == 'heartbeat':
            if not seat.left_at:
                seat.last_seen_at = now
                seat.save(update_fields=['last_seen_at'])
        else:
            raise voice.CallError('invalid_request', 400)
        if name != 'media':
            return serialize(seat, device)
    raise voice.CallError('expired')


def current(user, device):
    seat = GroupVoiceParticipant.objects.filter(user=user, left_at__isnull=True).select_related('call').first()
    return serialize(seat, device) if seat else None


def admit(room_id, subject):
    with transaction.atomic():
        call = GroupVoiceCall.objects.select_for_update().get(pk=room_id)
        expire(call, timezone.now())
        return call.participants.filter(pk=subject, left_at__isnull=True).exists()


def webhook(room_id, body):
    event = body.get('event')
    if event not in ('participant_joined', 'track_published', 'participant_left'):
        return
    subject = body.get('participant', {}).get('identity')
    if not subject:
        return
    with transaction.atomic():
        call = GroupVoiceCall.objects.select_for_update().get(pk=room_id)
        now = timezone.now()
        expire(call, now)
        seat = call.participants.filter(pk=subject, left_at__isnull=True).first()
        if not seat:
            return
        if event == 'participant_left':
            leave(seat, now)
        elif not seat.joined_at:
            rows = voice_media.rpc('ListParticipants', call).get('participants', [])
            ready = any(row.get('identity') == subject and any(
                track.get('source') in ('MICROPHONE', 2) for track in row.get('tracks', [])) for row in rows)
            if ready:
                seat.joined_at = now
                seat.expires_at = min(now + timedelta(seconds=seat.reserved_seconds), voice.week_end(seat.week))
                seat.save(update_fields=['joined_at', 'expires_at'])
                if not call.connected_at:
                    call.connected_at = now
                    call.save(update_fields=['connected_at'])
                    voice.update_message(call, record=record(call))


def reconcile(limit):
    active = GroupVoiceCall.objects.filter(ended_at__isnull=True).values_list('pk', flat=True)
    for call_id in active.iterator(chunk_size=limit):
        with transaction.atomic():
            call = GroupVoiceCall.objects.select_for_update().get(pk=call_id)
            expire(call, timezone.now())
    removed = GroupVoiceParticipant.objects.filter(left_at__isnull=False, media_removed_at__isnull=True,
                                                   call__ended_at__isnull=True).select_related('call')
    for seat in removed.order_by('left_at')[:limit]:
        try:
            voice_media.rpc('RemoveParticipant', seat.call, {'identity': str(seat.id)})
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                continue
        except requests.RequestException:
            continue
        GroupVoiceParticipant.objects.filter(pk=seat.pk).update(media_removed_at=timezone.now())
    for call in GroupVoiceCall.objects.filter(ended_at__isnull=False, media_closed_at__isnull=True).order_by('ended_at')[:limit]:
        if voice_media.close_room(call):
            GroupVoiceCall.objects.filter(pk=call.pk).update(media_closed_at=timezone.now())
