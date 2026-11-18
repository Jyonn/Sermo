"""Short-lived authenticated PCM relay; audio and transcripts are never persisted.

Run one ASGI worker: ticket replay prevention, concurrency and cost limits are
process-local. The normal WSGI API continues serving all other requests.
"""
import asyncio
import json
import os
import re
import time
from collections import deque
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core import signing
from django.db import close_old_connections
from django.http import JsonResponse
from django.views import View

from utils import auth

SALT = 'frienden.live-transcription.v1'
MAX_BYTES = 60 * 16000 * 2
ORIGINS = {'https://' + site for site in (
    'frienden.jyonn.space', 'sermo.jyonn.space', 'frienden.6-79.cn', 'sermo.6-79.cn')}


def provider_config():
    key = os.environ.get('DASHSCOPE_API_KEY', '').strip()
    workspace = os.environ.get('DASHSCOPE_WORKSPACE_ID', '').strip()
    region = os.environ.get('DASHSCOPE_REGION', 'cn-beijing').strip()
    if not key or region not in ('cn-beijing', 'ap-southeast-1'):
        return None
    if workspace:
        if not re.fullmatch(r'[A-Za-z0-9-]{1,63}', workspace):
            return None
        host = f'{workspace}.{region}.maas.aliyuncs.com'
    else:
        host = 'dashscope.aliyuncs.com' if region == 'cn-beijing' else 'dashscope-intl.aliyuncs.com'
    return key, f'wss://{host}/api-ws/v1/inference'


def ticket_response(principal):
    if not provider_config():
        response = JsonResponse({'identifier': 'VOICE_ASR_UNAVAILABLE', 'body': None}, status=503)
    else:
        ticket = signing.dumps({'principal': principal, 'nonce': uuid4().hex}, salt=SALT)
        response = JsonResponse({'identifier': 'OK', 'body': {'ticket': ticket, 'expires_in': 60}})
    response['Cache-Control'] = 'private, no-store'
    return response


class VoiceStreamTicketView(View):
    @auth.require_user
    def post(self, request):
        return ticket_response(f'user:{request.user.id}')


class AdminVoiceStreamTicketView(View):
    @auth.require_space
    def post(self, request):
        return ticket_response(f'space:{request.space.id}')


@sync_to_async(thread_sensitive=True)
def validate_principal(principal):
    close_old_connections()
    try:
        kind, identifier = principal.split(':')
        if kind == 'user':
            auth.User.jwt_login(int(identifier))
        elif kind == 'space':
            auth.Space.index(int(identifier))
        else:
            raise ValueError('Invalid principal')
    finally:
        close_old_connections()


class Admission:
    def __init__(self):
        self.used = {}
        self.active = set()
        self.starts = {}

    def claim(self, ticket):
        payload = signing.loads(ticket, salt=SALT, max_age=60)
        principal, nonce = payload['principal'], payload['nonce']
        now = time.monotonic()
        self.used = {key: expiry for key, expiry in self.used.items() if expiry > now}
        self.starts = {key: deque(t for t in times if t > now - 3600)
                       for key, times in self.starts.items() if times and times[-1] > now - 3600}
        history = self.starts.get(principal, deque())
        if nonce in self.used or principal in self.active or len(self.active) >= 32:
            raise ValueError('Busy or reused ticket')
        if len(history) >= 120 or sum(t > now - 60 for t in history) >= 12:
            raise ValueError('Rate limit')
        self.used[nonce] = now + 65
        history.append(now)
        self.starts[principal] = history
        self.active.add(principal)
        return principal


admission = Admission()


class Sentences:
    def __init__(self):
        self.rows = {}

    def update(self, sentence):
        if sentence.get('heartbeat'):
            return None
        number, text = sentence.get('sentence_id'), sentence.get('text')
        if type(number) is not int or not 0 <= number < 1000 or not isinstance(text, str):
            return None
        final = sentence.get('sentence_end') is True
        if self.rows.get(number, ('', False))[1] and not final:
            return None
        self.rows[number] = (text, final)
        if len(self.text()) > 12000:
            raise ValueError('Transcript too large')
        return {'type': 'transcript', 'text': self.text(),
                'final_text': self.text(True), 'partial_text': self.text(False)}

    def text(self, final=None):
        return '\n'.join(text for _, (text, done) in sorted(self.rows.items())
                         if text and (final is None or done is final))


def allowed_origin(scope):
    origin = dict(scope.get('headers', [])).get(b'origin', b'').decode('ascii', errors='ignore')
    if origin in ORIGINS:
        return True
    return settings.DEBUG and bool(re.fullmatch(r'http://(?:localhost|127\.0\.0\.1):\d+', origin))


async def stream_application(scope, receive, send):
    if scope['type'] != 'websocket':
        await send({'type': 'http.response.start', 'status': 404, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})
        return
    if scope['path'] != '/ws/voice-transcription' or not allowed_origin(scope):
        await send({'type': 'websocket.close', 'code': 4403})
        return
    if (await receive())['type'] != 'websocket.connect':
        return
    await send({'type': 'websocket.accept'})
    principal = None
    tasks = []

    async def emit(data):
        await send({'type': 'websocket.send', 'text': json.dumps(data, ensure_ascii=False)})

    try:
        first = await asyncio.wait_for(receive(), 5)
        raw = first.get('text', '')
        if first['type'] != 'websocket.receive' or not raw or len(raw) > 2000:
            raise ValueError('Authentication required')
        principal = admission.claim(json.loads(raw)['ticket'])
        await validate_principal(principal)
        connection = provider_config()
        if not connection:
            raise ValueError('Unavailable')
        from websockets.asyncio.client import connect
        key, url = connection
        task_id = uuid4().hex
        # Deadline includes connection, recording and finalization; no idle relay.
        async with asyncio.timeout(90), connect(url, additional_headers={'Authorization': f'Bearer {key}'},
                open_timeout=8, close_timeout=2, max_size=256 * 1024) as upstream:
            await upstream.send(json.dumps({
                'header': {'action': 'run-task', 'task_id': task_id, 'streaming': 'duplex'},
                'payload': {'task_group': 'audio', 'task': 'asr', 'function': 'recognition',
                    'model': os.environ.get('QWEN_STREAM_MODEL', 'qwen-audio-3.1-asr-flash-message'),
                    'input': {}, 'parameters': {'format': 'pcm', 'sample_rate': 16000,
                        'intermediate_result_enabled': True, 'disfluency_removal_enabled': False,
                        'keep_dialect': True, 'vad_model': 'near_meeting_16k', 'heartbeat': True}},
            }))
            started = json.loads(await asyncio.wait_for(upstream.recv(), 10))
            if started.get('header', {}).get('event') != 'task-started':
                raise ValueError('Provider unavailable')
            await emit({'type': 'ready'})
            sentences = Sentences()
            finished_input = False
            byte_size = 0

            async def read_results():
                async for raw in upstream:
                    message = json.loads(raw)
                    event = message.get('header', {}).get('event')
                    if event == 'result-generated':
                        update = sentences.update(message.get('payload', {}).get('output', {}).get('sentence', {}))
                        if update:
                            await emit(update)
                    elif event == 'task-finished':
                        if not finished_input:
                            raise ValueError('Premature end')
                        await emit({'type': 'done', 'text': sentences.text()})
                        return
                    elif event == 'task-failed':
                        raise ValueError('Provider failed')
                raise ValueError('Provider disconnected')

            async def write_audio():
                nonlocal byte_size, finished_input
                while True:
                    message = await asyncio.wait_for(receive(), 12)
                    if message['type'] != 'websocket.receive':
                        raise ValueError('Client disconnected')
                    chunk = message.get('bytes')
                    if chunk is not None:
                        if not chunk or len(chunk) > 32000 or len(chunk) % 2:
                            raise ValueError('Invalid frame')
                        byte_size += len(chunk)
                        if byte_size > MAX_BYTES:
                            raise ValueError('Recording too long')
                        await upstream.send(chunk)
                    else:
                        raw = message.get('text', '')
                        if len(raw) > 100 or json.loads(raw).get('type') != 'finish':
                            raise ValueError('Invalid command')
                        finished_input = True
                        await upstream.send(json.dumps({
                            'header': {'action': 'finish-task', 'task_id': task_id, 'streaming': 'duplex'},
                            'payload': {'input': {}},
                        }))
                        return

            reader = asyncio.create_task(read_results())
            writer = asyncio.create_task(write_audio())
            tasks = [reader, writer]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if writer in done and not reader.done():
                await asyncio.wait_for(reader, 8)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Never expose/log provider errors, credentials, audio or recognized text.
        try:
            await emit({'type': 'error', 'error': 'transcription_unavailable'})
        except Exception:
            pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if principal:
            admission.active.discard(principal)
        try:
            await send({'type': 'websocket.close', 'code': 1000})
        except Exception:
            pass
