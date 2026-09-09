from datetime import timedelta

from django.utils import timezone


CHAT_MUTE_DURATIONS = {
    '10s': 10,
    '2m': 2 * 60,
    '10m': 10 * 60,
    '1h': 60 * 60,
    '6h': 6 * 60 * 60,
    '1d': 24 * 60 * 60,
    '7d': 7 * 24 * 60 * 60,
    'permanent': None,
}


def resolve_chat_mute(duration, now=None):
    if duration not in CHAT_MUTE_DURATIONS:
        raise ValueError('invalid chat mute duration')
    seconds = CHAT_MUTE_DURATIONS[duration]
    if seconds is None:
        return True, None
    return False, (now or timezone.now()) + timedelta(seconds=seconds)


def chat_mute_payload(permanent=False, muted_until=None, now=None):
    active = bool(permanent or (muted_until and muted_until > (now or timezone.now())))
    return {
        'active': active,
        'permanent': bool(permanent and active),
        'muted_until': muted_until.timestamp() if active and muted_until else None,
    }
