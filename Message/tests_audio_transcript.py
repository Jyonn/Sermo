import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from Chat.models import Chat
from Friendship.models import Friendship
from Message.models import AudioTranscript, AudioTranscriptStatusChoice, MediaAsset, MediaResource, Message, MessageTypeChoice
from Space.models import Space
from User.models import User
from utils import auth
from utils.qiniu import ShortAudioTranscriptionError


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
        transcript = AudioTranscript.objects.get(asset=self.message.media_resource.asset)
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
            asset=self.message.media_resource.asset,
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
        self.assertFalse(AudioTranscript.objects.filter(asset=self.message.media_resource.asset).exists())

    @patch('Message.views.sign_private_download_url', return_value='https://signed.example.com/audio.m4a')
    @patch('Message.views.transcribe_short_audio', return_value=('转发后仍然复用。', 'request-forward'))
    def test_audio_resource_reuses_asset_transcript(self, transcribe, sign):
        target_peer = User.create(self.space, 'Target', email='target@example.com', verified=True)
        Friendship.ensure_locked_friendship(self.user, target_peer)
        target_chat = Chat.get_or_create_direct(self.user, target_peer)

        first = self.client.post(self.endpoint(), data='{}', content_type='application/json', **self.authorization(self.user))
        reused_resource = self.message.media_resource.clone_for(self.user)
        reused = Message.objects.create(
            chat=target_chat, user=self.user, type=MessageTypeChoice.AUDIO,
            content=self.message.content, media_resource=reused_resource,
        )
        reused_endpoint = f'/messages/audio-transcript?message_id={reused.id}'
        second = self.client.post(reused_endpoint, data='{}', content_type='application/json', **self.authorization(self.user))

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json()['body'], {
            'status': 'ready',
            'text': '转发后仍然复用。',
            'cached': True,
        })
        self.assertEqual(reused.media_resource.asset_id, self.message.media_resource.asset_id)
        self.assertEqual(AudioTranscript.objects.count(), 1)
        transcribe.assert_called_once_with('https://signed.example.com/audio.m4a')
        sign.assert_called_once()
