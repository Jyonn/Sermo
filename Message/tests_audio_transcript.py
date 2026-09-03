import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from Chat.models import Chat
from Friendship.models import Friendship
from Message.models import AudioTranscript, AudioTranscriptStatusChoice, MediaAsset, MediaResource, Message, MessageTypeChoice
from Space.models import Space
from User.models import User
from utils import auth
from utils.qiniu import QINIU_SHORT_AUDIO_ASR_URL, QiniuMacRequestsAuth, ShortAudioTranscriptionError, transcribe_short_audio


class QiniuShortAudioTranscriptionTests(SimpleTestCase):
    @patch('utils.qiniu._required_config', side_effect=['access-key', 'secret-key'])
    @patch('utils.qiniu.requests.post')
    def test_request_uses_qiniu_mac_and_returns_transcript(self, post, _required_config):
        response = Mock()
        response.json.return_value = {
            'rtn': 0,
            'requestId': 'request-1',
            'resultText': ' 今天见。 ',
        }
        response.raise_for_status.return_value = None
        post.return_value = response

        text, request_id = transcribe_short_audio('https://media.example.com/audio.m4a')

        self.assertEqual(text, '今天见。')
        self.assertEqual(request_id, 'request-1')
        self.assertEqual(post.call_args.args[0], QINIU_SHORT_AUDIO_ASR_URL)
        self.assertEqual(post.call_args.kwargs['json'], {
            'audioUrl': 'https://media.example.com/audio.m4a',
            'lang': 'MANDARIN',
            'scene': 'GENERAL',
        })
        self.assertIsInstance(post.call_args.kwargs['auth'], QiniuMacRequestsAuth)

    @patch('utils.qiniu._required_config', side_effect=['access-key', 'secret-key'])
    @patch('utils.qiniu.requests.post')
    def test_provider_failure_is_normalized(self, post, _required_config):
        response = Mock()
        response.json.return_value = {
            'rtn': 4204,
            'requestId': 'request-2',
            'message': 'unsupported format',
        }
        response.raise_for_status.return_value = None
        post.return_value = response

        with self.assertRaises(ShortAudioTranscriptionError) as raised:
            transcribe_short_audio('https://media.example.com/audio.webm')

        self.assertEqual(raised.exception.code, 4204)
        self.assertEqual(raised.exception.request_id, 'request-2')


class MessageAudioTranscriptTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Transcript',
            slug='transcript',
            email='owner@example.com',
        )
        self.user = User.create(self.space, 'Listener', email='listener@example.com', verified=True)
        self.peer = User.create(self.space, 'Speaker', email='speaker@example.com', verified=True)
        self.outsider = User.create(self.space, 'Outsider', email='outsider@example.com', verified=True)
        Friendship.ensure_locked_friendship(self.user, self.peer)
        self.chat = Chat.get_or_create_direct(self.user, self.peer)
        asset = MediaAsset.objects.create(
            source_key='sermo/messages/audio/source.m4a',
            source_uri='https://media.example.com/sermo/messages/audio/source.m4a',
            kind=MediaAsset.KIND_AUDIO,
            status=MediaAsset.STATUS_READY,
            duration_seconds=8,
        )
        resource = MediaResource.acquire(self.peer, asset, MediaAsset.KIND_AUDIO, 'source.m4a')
        self.message = Message.objects.create(
            chat=self.chat,
            user=self.peer,
            type=MessageTypeChoice.AUDIO,
            content=json.dumps({'kind': 'audio', 'uri': asset.source_uri, 'duration_seconds': 8}),
            media_resource=resource,
        )

    @staticmethod
    def authorization(user):
        return {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(user)['auth']}"}

    def endpoint(self):
        return f'/messages/audio-transcript?message_id={self.message.id}'

    @patch('Message.views.sign_private_download_url', return_value='https://signed.example.com/audio.m4a')
    @patch('Message.views.transcribe_short_audio', return_value=('你好，明天见。', 'request-3'))
    def test_first_click_transcribes_and_later_click_uses_cache(self, transcribe, sign):
        first = self.client.post(self.endpoint(), data='{}', content_type='application/json', **self.authorization(self.user))
        second = self.client.post(self.endpoint(), data='{}', content_type='application/json', **self.authorization(self.user))

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()['body'], {
            'status': 'ready',
            'text': '你好，明天见。',
            'cached': False,
        })
        self.assertEqual(second.json()['body']['cached'], True)
        transcribe.assert_called_once_with('https://signed.example.com/audio.m4a')
        sign.assert_called_once()
        transcript = AudioTranscript.objects.get(message=self.message)
        self.assertEqual(transcript.text, '你好，明天见。')
        self.assertEqual(transcript.provider_request_id, 'request-3')

    @patch('Message.views.sign_private_download_url', return_value='https://signed.example.com/audio.m4a')
    @patch('Message.views.transcribe_short_audio')
    def test_failed_transcription_can_be_retried(self, transcribe, _sign):
        transcribe.side_effect = [
            ShortAudioTranscriptionError('busy', code=4203, request_id='request-4'),
            ('重试成功。', 'request-5'),
        ]

        failed = self.client.post(self.endpoint(), data='{}', content_type='application/json', **self.authorization(self.user))
        retried = self.client.post(self.endpoint(), data='{}', content_type='application/json', **self.authorization(self.user))

        self.assertEqual(failed.json()['body']['status'], 'failed')
        self.assertEqual(retried.json()['body']['status'], 'ready')
        self.assertEqual(retried.json()['body']['text'], '重试成功。')
        self.assertEqual(transcribe.call_count, 2)

    @patch('Message.views.transcribe_short_audio')
    def test_recent_processing_request_is_not_duplicated(self, transcribe):
        AudioTranscript.objects.create(
            message=self.message,
            status=AudioTranscriptStatusChoice.PROCESSING,
        )

        response = self.client.post(self.endpoint(), data='{}', content_type='application/json', **self.authorization(self.user))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body']['status'], 'processing')
        transcribe.assert_not_called()

    def test_non_member_cannot_transcribe_message(self):
        response = self.client.post(self.endpoint(), data='{}', content_type='application/json', **self.authorization(self.outsider))

        self.assertEqual(response.status_code, 403, response.content)

    def test_get_does_not_start_transcription(self):
        response = self.client.get(self.endpoint(), **self.authorization(self.user))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body']['status'], 'none')
        self.assertFalse(AudioTranscript.objects.filter(message=self.message).exists())
