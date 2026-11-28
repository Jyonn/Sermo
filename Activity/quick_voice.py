"""Short-lived preset messages for players seated in the same room."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error

from Activity.table_activity import TABLE_MODES, activity_run, room_code


COMMON_PHRASES = ('hello', 'wait', 'good_play', 'close', 'thanks', 'next_round')
CARD_PHRASES = ('good_hand', 'your_turn')
EXTRA_PHRASES = {**dict.fromkeys(TABLE_MODES, CARD_PHRASES), 'number_duel': ('your_call', 'your_guess')}
LIFETIME = 8
COOLDOWN = 3
EVENT_LIMIT = 12


@Error.register
class QuickVoiceErrors:
    UNAVAILABLE = Error(_('Quick voice is unavailable in this room'), code=Code.Forbidden)
    INVALID = Error(_('Invalid quick voice preset'), code=Code.BadRequest)
    COOLDOWN = Error(_('Wait before sending another quick voice message'), code=Code.BadRequest)


def can_send_at(room, now):
    from Activity.models import NumberDuel, LandlordTable

    if isinstance(room, NumberDuel):
        if room.status == 'finished':
            return bool(room.reason in ('normal', 'draw') and room.finished_at and
                        room.finished_at > now - timedelta(minutes=2))
        return room.status in ('choosing', 'secrets', 'playing') and room.deadline > now

    from Activity import four_player
    from Activity.landlord import LandlordService

    if isinstance(room, LandlordTable):
        if room.status not in LandlordService.LIVE:
            return False
        timeout = LandlordService.WAIT_LIMIT if room.status in LandlordService.BETWEEN_ROUNDS else LandlordService.IDLE_LIMIT
    else:
        if room.status not in four_player.LIVE:
            return False
        timeout = four_player.WAIT if room.status in four_player.BETWEEN_ROUNDS else four_player.IDLE
    return room.updated_at > now - timeout


def visible_voice(room, members, user_id, now):
    enabled = len(members) > 1 and user_id in members and can_send_at(room, now)
    stamp = now.timestamp()
    messages = [
        {key: event[key] for key in ('id', 'user_id', 'phrase', 'sent_at')}
        for event in (room.quick_voice_events or [])
        if event['sent_at'] + LIFETIME > stamp and event['user_id'] in members and user_id in event['audience']
    ] if enabled else []
    return dict(enabled=enabled, now=stamp, messages=messages)


def room_members(mode, run, code, user):
    from Activity.models import FourPlayerTable, LandlordTable, NumberDuel
    from Activity.number_duel import NumberDuelService
    from Activity.table_activity import eligible

    if mode == 'number_duel':
        room = NumberDuel.objects.select_for_update().filter(run=run, code=code).filter(
            Q(host=user) | Q(guest=user),
        ).first()
        people = (room.host, room.guest) if room else ()
        return room, [person.pk for person in people if person and NumberDuelService.eligible(person)]

    model = LandlordTable if mode == 'landlord' else FourPlayerTable
    room = model.objects.select_for_update().filter(run=run, code=code, seats__user=user).first()
    people = room.seats.select_related('user').all() if room else ()
    return room, [seat.user_id for seat in people if eligible(seat.user)]


@transaction.atomic
def send_voice(key, user, data):
    if not isinstance(data, dict):
        raise QuickVoiceErrors.INVALID
    phrase = data.get('phrase')
    if not isinstance(phrase, str):
        raise QuickVoiceErrors.INVALID

    code = room_code(data.get('code'))
    run = activity_run(key, user, (*TABLE_MODES, 'number_duel'))
    if not run or not code:
        raise QuickVoiceErrors.UNAVAILABLE
    mode = run.campaign.config['mode']
    if phrase not in (*COMMON_PHRASES, *EXTRA_PHRASES[mode]):
        raise QuickVoiceErrors.INVALID

    room, members = room_members(mode, run, code, user)
    now = timezone.now()
    if not room or user.pk not in members or len(members) < 2 or not can_send_at(room, now):
        raise QuickVoiceErrors.UNAVAILABLE
    stamp = now.timestamp()
    history = room.quick_voice_events or []
    recent = [event for event in history if event['sent_at'] + LIFETIME > stamp]
    if any(event['user_id'] == user.pk and event['sent_at'] + COOLDOWN > stamp for event in recent):
        raise QuickVoiceErrors.COOLDOWN

    event_id = history[-1]['id'] + 1 if history else 1
    recent.append(dict(id=event_id, user_id=user.pk, phrase=phrase, sent_at=stamp, audience=members))
    room.quick_voice_events = recent[-EVENT_LIMIT:]
    room.save(update_fields=['quick_voice_events'])
    return visible_voice(room, members, user.pk, now)
