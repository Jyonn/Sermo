from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from utils.webpush import send_web_push


class WebPushTransportTests(SimpleTestCase):
    @patch('utils.webpush.vapid_public_key', return_value='public')
    @patch('utils.webpush.Globals.WEB_PUSH_VAPID_SUBJECT', 'mailto:admin@example.com', create=True)
    @patch('utils.webpush.Globals.WEB_PUSH_VAPID_PRIVATE_KEY', 'private', create=True)
    @patch('utils.webpush.webpush')
    def test_transport_has_a_bounded_timeout(self, webpush, _public_key):
        subscription = SimpleNamespace(endpoint='https://example.com/push', p256dh='key', auth='auth')

        send_web_push(subscription, 'Title', 'Body', {})

        self.assertEqual(webpush.call_args.kwargs['timeout'], 8)
        self.assertEqual(webpush.call_args.kwargs['ttl'], 300)
