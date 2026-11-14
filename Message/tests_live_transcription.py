import asyncio
import json
from unittest.mock import AsyncMock, patch

from asgiref.testing import ApplicationCommunicator
from django.core import signing
from django.test import SimpleTestCase, TestCase

from Message import live_transcription as live


class TranscriptTests(SimpleTestCase):
    def test_partial_replaces_partial_but_never_overwrites_final(self):
        sentences = live.Sentences()
        sentences.update({'sentence_id': 1, 'text': 'hello', 'sentence_end': False})
        sentences.update({'sentence_id': 1, 'text': 'hello world', 'sentence_end': True})
        self.assertIsNone(sentences.update({'sentence_id': 1, 'text': 'stale'}))
        self.assertIsNone(sentences.update({'heartbeat': True}))
        result = sentences.update({'sentence_id': 2, 'text': 'next'})
        self.assertEqual(result['text'], 'hello world\nnext')
        self.assertEqual(result['final_text'], 'hello world')

    def test_ticket_is_one_use_expiring_and_concurrency_bounded(self):
        admission = live.Admission()
        ticket = lambda nonce: signing.dumps({'principal': 'user:1', 'nonce': nonce}, salt=live.SALT)
        admission.claim(ticket('a'))
        with self.assertRaises(ValueError):
            admission.claim(ticket('b'))
        admission.active.clear()
        with self.assertRaises(ValueError):
            admission.claim(ticket('a'))
        with patch('django.core.signing.time.time', return_value=1):
            expired = ticket('expired')
        with self.assertRaises(signing.SignatureExpired):
            admission.claim(expired)

    def test_origin_is_exact(self):
        for origin in live.ORIGINS:
            self.assertTrue(live.allowed_origin({'headers': [(b'origin', origin.encode())]}))
        self.assertFalse(live.allowed_origin({'headers': [(b'origin', b'https://frienden.jyonn.space.attacker.test')]}))

    async def test_invalid_ticket_never_connects_to_provider(self):
        with patch('websockets.asyncio.client.connect') as provider:
            client = ApplicationCommunicator(live.stream_application, {'type': 'websocket', 'path': '/ws/voice-transcription', 'headers': [(b'origin', b'https://frienden.jyonn.space')]})
            await client.send_input({'type': 'websocket.connect'})
            await client.receive_output()
            await client.send_input({'type': 'websocket.receive', 'text': '{"ticket":"invalid"}'})
            self.assertEqual(json.loads((await client.receive_output())['text'])['type'], 'error')
            await client.wait()
            provider.assert_not_called()

    async def test_relay_finalizes_and_disconnect_cancels_provider(self):
        for disconnect in (False, True):
            events = asyncio.Queue()
            class Provider:
                closed = False
                sent = []
                async def __aenter__(self): return self
                async def __aexit__(self, *_): self.closed = True
                async def send(self, data):
                    self.sent.append(data)
                    if isinstance(data, bytes):
                        await events.put(json.dumps({'header': {'event': 'result-generated'}, 'payload': {'output': {'sentence': {'sentence_id': 0, 'text': '测试', 'sentence_end': True}}}}))
                    elif json.loads(data)['header']['action'] == 'finish-task':
                        await events.put(json.dumps({'header': {'event': 'task-finished'}}))
                async def recv(self): return json.dumps({'header': {'event': 'task-started'}})
                def __aiter__(self): return self
                async def __anext__(self): return await events.get()
            provider = Provider()
            admission = live.Admission()
            with patch.object(live, 'admission', admission), patch.object(live, 'validate_principal', new=AsyncMock()), patch.object(live, 'provider_config', return_value=('private-key', 'wss://test')), patch('websockets.asyncio.client.connect', return_value=provider):
                client = ApplicationCommunicator(live.stream_application, {'type': 'websocket', 'path': '/ws/voice-transcription', 'headers': [(b'origin', b'https://frienden.jyonn.space')]})
                await client.send_input({'type': 'websocket.connect'})
                self.assertEqual((await client.receive_output())['type'], 'websocket.accept')
                ticket = signing.dumps({'principal': 'user:1', 'nonce': 'test'}, salt=live.SALT)
                await client.send_input({'type': 'websocket.receive', 'text': json.dumps({'ticket': ticket})})
                self.assertEqual(json.loads((await client.receive_output())['text'])['type'], 'ready')
                await client.send_input({'type': 'websocket.receive', 'bytes': bytes(3200)})
                self.assertEqual(json.loads((await client.receive_output())['text'])['text'], '测试')
                await client.send_input({'type': 'websocket.disconnect'} if disconnect else {'type': 'websocket.receive', 'text': '{"type":"finish"}'})
                result = json.loads((await client.receive_output())['text'])
                self.assertEqual(result['type'], 'error' if disconnect else 'done')
                self.assertNotIn('private-key', json.dumps(result))
                await client.wait()
                self.assertTrue(provider.closed)
                self.assertEqual(admission.active, set())


class TicketTests(TestCase):
    def test_auth_required_and_grants_never_expose_provider_key(self):
        from Space.models import Space
        from User.models import User
        from utils import auth
        space = Space.objects.create(slug='live-test', name='Live', email='live@example.com')
        user = User.create(space, 'Listener', verified=True)
        response = self.client.post('/messages/voice-stream-ticket')
        self.assertNotEqual(response.json().get('identifier'), 'OK')
        with patch.object(live, 'provider_config', return_value=('private-key', 'wss://test')):
            response = self.client.post('/messages/voice-stream-ticket', HTTP_AUTHORIZATION='Bearer '+auth.get_login_token(user)['auth'])
            self.assertEqual(response.status_code, 200)
            body = response.json()['body']
            self.assertEqual(signing.loads(body['ticket'], salt=live.SALT)['principal'], f'user:{user.id}')
            self.assertNotIn('private-key', response.content.decode())
            self.assertIn('no-store', response['Cache-Control'])
            admin = self.client.post('/spaces/admin/voice-stream-ticket', HTTP_AUTHORIZATION='Bearer '+auth.get_space_login_token(space)['auth'])
            self.assertEqual(signing.loads(admin.json()['body']['ticket'], salt=live.SALT)['principal'], f'space:{space.id}')
