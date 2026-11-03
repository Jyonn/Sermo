from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from utils.webpush import send_web_push, notification_payload
import json


class WebPushTransportTests(SimpleTestCase):
    def test_large_unicode_messages_preserve_routing_and_receipts_within_transport_limit(self):
        encoded = notification_payload('测试'*1000, '你好😀"\\'*5000, {'chat_id':123,'receipt_token':'signed-receipt','content':'duplicate'*10000,'private_field':'not for push'})
        self.assertLessEqual(len(encoded.encode('utf-8')), 3000)
        data=json.loads(encoded)
        self.assertEqual(data['chat_id'],123)
        self.assertEqual(data['receipt_token'],'signed-receipt')
        self.assertNotIn('content',data)
        self.assertNotIn('private_field',data)
    @patch('utils.webpush.vapid_public_key', return_value='public')
    @patch('utils.webpush.Globals.WEB_PUSH_VAPID_SUBJECT', 'mailto:admin@example.com', create=True)
    @patch('utils.webpush.Globals.WEB_PUSH_VAPID_PRIVATE_KEY', 'private', create=True)
    @patch('utils.webpush.webpush')
    def test_transport_has_a_bounded_timeout(self, webpush, _public_key):
        subscription = SimpleNamespace(endpoint='https://example.com/push', p256dh='key', auth='auth')

        send_web_push(subscription, 'Title', 'Body', {})

        self.assertEqual(webpush.call_args.kwargs['timeout'], 8)
        self.assertEqual(webpush.call_args.kwargs['ttl'], 300)
