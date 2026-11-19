import json
from unittest.mock import Mock, patch
from django.test import TestCase, SimpleTestCase
from Message import tests_audio_transcript as audio_fixture
from Message.recording_transcript import attach, receipt
from Message.models import AudioTranscript, MediaAsset, MediaResource, Message
from Message.aliyun_transcription import transcribe_short_audio
from utils.qiniu import ShortAudioTranscriptionError


class RecordingResultTests(TestCase):
    def setUp(self):
        audio_fixture.MessageAudioTranscriptTests.setUp(self)

    @patch('User.models.User.require_capability')
    @patch('User.models.NotificationEvent.emit_message_notifications')
    def test_send_endpoint_binds_signed_result_in_same_transaction(self, notify, capability):
        proof = receipt(f'user:{self.peer.id}', '发送时保存的实时结果')
        response = self.client.post(f'/messages/?chat_id={self.chat.id}', data=json.dumps({
            'type': self.message.type, 'content': '', 'resource_id': self.message.media_resource_id,
            'client_message_id': 'live-recording-test', 'live_transcript_receipt': proof,
        }), content_type='application/json', **audio_fixture.MessageAudioTranscriptTests.authorization(self.peer))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(AudioTranscript.objects.get(asset=self.message.media_resource.asset).text, '发送时保存的实时结果')

    @patch('Message.views.transcribe_short_audio')
    def test_recording_result_is_ready_and_no_second_provider_call(self, provider):
        proof = receipt(f'user:{self.peer.id}', '已经实时识别的文本。')
        self.assertTrue(attach(self.message, self.peer, proof))
        result = self.client.post(f'/messages/audio-transcript?message_id={self.message.id}', data='{}', content_type='application/json', **audio_fixture.MessageAudioTranscriptTests.authorization(self.user))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['body']['text'], '已经实时识别的文本。')
        self.assertTrue(result.json()['body']['cached'])
        provider.assert_not_called()

    def test_wrong_user_and_tampered_receipts(self):
        self.assertFalse(attach(self.message, self.peer, receipt(f'user:{self.user.id}', 'Wrong user')))
        self.assertFalse(attach(self.message, self.peer, receipt(f'user:{self.peer.id}', 'Text') + 'tampered'))
        self.assertFalse(AudioTranscript.objects.exists())

    def test_completed_silence_is_cached_without_another_call(self):
        self.assertTrue(attach(self.message, self.peer, receipt(f'user:{self.peer.id}', '')))
        self.assertEqual(AudioTranscript.objects.get(asset=self.message.media_resource.asset).jsonl(cached=True), {'status':'ready','text':'','cached':True})

    def test_receipt_cannot_poison_another_asset_or_overwrite(self):
        proof = receipt(f'user:{self.peer.id}', 'First')
        self.assertTrue(attach(self.message, self.peer, proof))
        self.assertTrue(attach(self.message, self.peer, proof))
        self.assertFalse(attach(self.message, self.peer, receipt(f'user:{self.peer.id}', 'Overwrite')))
        asset = MediaAsset.objects.create(source_key='second.m4a', source_uri='https://media.example.com/second.m4a', kind=MediaAsset.KIND_AUDIO)
        resource = MediaResource.acquire(self.peer, asset, MediaAsset.KIND_AUDIO, 'second.m4a')
        second = Message.objects.create(chat=self.chat, user=self.peer, type=self.message.type, media_resource=resource)
        self.assertFalse(attach(second, self.peer, proof))
        self.assertFalse(AudioTranscript.objects.filter(asset=asset).exists())


class AliyunASRTests(SimpleTestCase):
    @patch('Message.aliyun_transcription.provider_config', return_value=('test-key', 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'))
    @patch('Message.aliyun_transcription.requests.post')
    def test_official_request_and_response(self, post, config):
        post.return_value.json.return_value = {'request_id': 'request-test', 'output': {'choices': [{'message': {'content': [{'text': '识别结果'}]}}]}}
        self.assertEqual(transcribe_short_audio('https://signed.example.com/audio.webm'), ('识别结果', 'request-test'))
        request = post.call_args.kwargs
        self.assertEqual(request['json']['model'], 'qwen3-asr-flash')
        self.assertEqual(request['json']['input']['messages'][0]['content'][0]['audio'], 'https://signed.example.com/audio.webm')

    @patch('Message.aliyun_transcription.provider_config', return_value=None)
    def test_no_secret_fallback_to_another_provider(self, config):
        with self.assertRaises(ShortAudioTranscriptionError):
            transcribe_short_audio('https://signed.example.com/audio.webm')
