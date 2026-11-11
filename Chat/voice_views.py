import json

from django.http import HttpResponse, JsonResponse
from django.views import View

from Chat import group_voice, voice
from Chat.models import Chat
from Chat.voice_models import GroupVoiceCall
from utils import auth


def response(fn):
    try:
        result = fn()
        value = JsonResponse(dict(body=result, identifier='OK'))
    except voice.CallError as exc:
        value = JsonResponse(dict(body=None, identifier='VOICE_'+exc.reason.upper(), message=exc.reason), status=exc.status)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        value = JsonResponse(dict(body=None, identifier='VOICE_INVALID_REQUEST', message='invalid_request'), status=400)
    value['Cache-Control'] = 'no-store, private'
    return value


class VoiceCapabilityView(View):
    @auth.require_user
    def get(self, request):
        def execute():
            chat = Chat.index(int(request.GET.get('chat_id', 0)))
            return (group_voice.capability if chat.group else voice.capability)(chat, request.user)
        return response(execute)


class VoiceCurrentView(View):
    @auth.require_user
    def get(self, request):
        def execute():
            if not voice.enabled():
                return dict(enabled=False, call=None)
            call = voice.current(request.user)
            device = request.GET.get('device_id')
            return dict(enabled=True, call=voice.serialize(call, request.user, device) if call else group_voice.current(request.user, device))
        return response(execute)


class VoiceCreateView(View):
    @auth.require_user
    def post(self, request):
        def execute():
            body = json.loads(request.body)
            chat = Chat.index(int(body['chat_id']))
            if chat.group:
                return group_voice.serialize(group_voice.join(chat, request.user, body['client_key'], body['device_id']), body['device_id'])
            call = voice.create(chat, request.user, body['client_key'], body['device_id'])
            return voice.serialize(call, request.user, body['device_id'])
        return response(execute)


class VoiceActionView(View):
    @auth.require_user
    def post(self, request, call_id, action):
        def execute():
            voice.require_enabled()
            body = json.loads(request.body)
            if GroupVoiceCall.objects.filter(pk=call_id).exists():
                return group_voice.action(call_id, request.user, action, body.get('device_id'))
            if action == 'media':
                from Chat.voice_media import token, configured
                with voice.locked_call(call_id, request.user) as call:
                    voice.expire_locked(call, voice.timezone.now())
                    if call.state not in ('connecting', 'active') or not configured():
                        result = None
                    elif not voice.owns(call, request.user, body.get('device_id')):
                        raise voice.CallError('other_device')
                    else:
                        result = dict(url=voice.settings.VOICE_LIVEKIT_URL, token=token(call, request.user))
                # Commit timeout settlement before reporting the expired credential.
                if result is None:
                    raise voice.CallError('expired')
                return result
            call = voice.mutate(call_id, request.user, action, body.get('device_id'))
            return voice.serialize(call, request.user, body.get('device_id'))
        return response(execute)


class VoiceMediaAdmissionView(View):
    """Nginx auth_request gate: an old SFU token cannot recreate an ended room."""
    def get(self, request):
        import jwt
        from django.conf import settings
        allowed = False
        try:
            voice.require_enabled()
            claims = jwt.decode(request.headers.get('X-Voice-Token', '').removeprefix('Bearer '),
                settings.VOICE_LIVEKIT_API_SECRET, algorithms=['HS256'],
                issuer=settings.VOICE_LIVEKIT_API_KEY,
                options={'require': ['exp', 'iss', 'sub']})
            grant = claims.get('video', {})
            room = grant.get('room', '')
            if not grant.get('roomJoin') or not room.startswith('frienden-'):
                raise voice.CallError('forbidden', 403)
            if room.startswith(group_voice.ROOM_PREFIX):
                allowed = group_voice.admit(room.removeprefix(group_voice.ROOM_PREFIX), claims['sub'])
            else:
                with voice.locked_call(room.removeprefix('frienden-')) as call:
                    voice.expire_locked(call, voice.timezone.now())
                    allowed = (call.state in ('connecting', 'active') and
                               claims['sub'] in (str(call.caller_id), str(call.callee_id)))
        except (voice.CallError, GroupVoiceCall.DoesNotExist, jwt.PyJWTError, ValueError, TypeError, AttributeError):
            pass
        result = HttpResponse(status=204 if allowed else 403)
        result['Cache-Control'] = 'no-store'
        return result


class VoiceWebhookView(View):
    def post(self, request):
        def execute():
            voice.require_enabled()
            import hashlib
            import hmac
            import base64
            import jwt
            from Chat.voice_media import rpc
            from django.conf import settings
            from django.utils import timezone
            try:
                claims = jwt.decode(request.headers.get('Authorization', '').removeprefix('Bearer '),
                    settings.VOICE_LIVEKIT_API_SECRET, algorithms=['HS256'], issuer=settings.VOICE_LIVEKIT_API_KEY,
                    options={'require':['exp', 'iss', 'sha256']})
                digest = base64.b64encode(hashlib.sha256(request.body).digest()).decode()
                if not hmac.compare_digest(claims['sha256'], digest):
                    raise voice.CallError('forbidden', 403)
            except jwt.PyJWTError:
                raise voice.CallError('forbidden', 403)
            body = json.loads(request.body)
            room = body.get('room', {}).get('name', '')
            if not room.startswith('frienden-'):
                return {}
            if room.startswith(group_voice.ROOM_PREFIX):
                try:
                    group_voice.webhook(room.removeprefix(group_voice.ROOM_PREFIX), body)
                except GroupVoiceCall.DoesNotExist:
                    pass
                return {}
            with voice.locked_call(room.removeprefix('frienden-')) as call:
                now = timezone.now()
                voice.expire_locked(call, now)
                if call.state == 'connecting' and body.get('event') in ('participant_joined', 'track_published'):
                    rows = rpc('ListParticipants', call).get('participants', [])
                    # Both expected users must have a published microphone; client reports cannot start billing.
                    ready = {p.get('identity') for p in rows if any(track.get('source') in ('MICROPHONE', 2) for track in p.get('tracks', []))}
                    if {str(call.caller_id), str(call.callee_id)} <= ready:
                        call.state, call.connected_at = 'active', now
                        call.deadline_at = min(now+voice.timedelta(seconds=call.max_seconds), voice.week_end(call.week))
                        call.save(update_fields=['state', 'connected_at', 'deadline_at'])
                        voice.update_message(call)
                elif call.state == 'active' and body.get('event') == 'participant_left':
                    voice._end_locked(call, 'disconnected', now)
            return {}
        return response(execute)
