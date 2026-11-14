"""Small LiveKit REST adapter. Credentials never enter participant payloads."""
import time
from urllib.parse import urlparse

import jwt
import requests
from django.conf import settings


def configured():
    return all(getattr(settings, name, '') for name in ('VOICE_LIVEKIT_URL', 'VOICE_LIVEKIT_API_KEY', 'VOICE_LIVEKIT_API_SECRET'))


def room_name(call):
    from Chat.voice_models import GroupVoiceCall
    prefix = 'frienden-group-' if isinstance(call, GroupVoiceCall) else 'frienden-'
    return f'{prefix}{call.id}'


def token(call, user, identity=None):
    now = int(time.time())
    return jwt.encode(dict(iss=settings.VOICE_LIVEKIT_API_KEY, sub=str(identity or user.id), name=user.name,
        nbf=now-2, exp=now+60, video=dict(roomJoin=True, room=room_name(call), canPublish=True,
        canPublishSources=['microphone'], canSubscribe=True, canPublishData=False, canUpdateOwnMetadata=False)),
        settings.VOICE_LIVEKIT_API_SECRET, algorithm='HS256')


def rpc(method, call, extra=None):
    now = int(time.time())
    auth = jwt.encode(dict(iss=settings.VOICE_LIVEKIT_API_KEY, nbf=now-2, exp=now+30,
        video=dict(roomAdmin=True, roomCreate=method in ('CreateRoom', 'DeleteRoom'), room=room_name(call))), settings.VOICE_LIVEKIT_API_SECRET, algorithm='HS256')
    parsed = urlparse(getattr(settings, 'VOICE_LIVEKIT_INTERNAL_URL', '') or settings.VOICE_LIVEKIT_URL)
    origin = ('https' if parsed.scheme in ('wss', 'https') else 'http')+'://'+parsed.netloc
    response = requests.post(f'{origin}{parsed.path.rstrip("/")}/twirp/livekit.RoomService/{method}',
        headers={'Authorization':f'Bearer {auth}'}, json=dict(room=room_name(call), **(extra or {})), timeout=3)
    response.raise_for_status()
    return response.json()


def close_room(call):
    if not configured():
        return True
    try:
        rpc('DeleteRoom', call)
        return True
    except requests.HTTPError as exc:
        return exc.response is not None and exc.response.status_code == 404
    except requests.RequestException:
        # Leave the DB cleanup marker unset: the worker will retry.
        return False
