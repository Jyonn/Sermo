import json
from unittest.mock import patch

from django.test import TestCase

from User import tests_native_push_bindings as fixtures
from User.models import NotificationEvent
from User.native_push_payload import native_push_payload


class NativePushPayloadTests(TestCase):
    setUp = fixtures.NativePushBindingTests.setUp

    def event(self):
        return NotificationEvent.objects.create(user=self.a, space=self.space, event_type=1,
            payload={'content': 'Hello', 'token': 'secret-token', 'url': 'https://evil.invalid', 'account_id': self.b.pk})

    @patch('User.models.NotificationDelivery._web_notification_text', return_value=('Title', 'Body'))
    def test_payload_uses_canonical_recipient_and_event_not_arbitrary_fields(self, text):
        event = self.event()
        payload = native_push_payload(event)
        self.assertEqual(set(payload), {'aps', 'frienden'})
        self.assertEqual(payload['frienden'], dict(version=1, account_id=self.a.pk, space_slug=self.space.slug,
                                                   notification_event_id=event.pk))
        self.assertEqual(payload['aps']['alert'], {'title': 'Title', 'body': 'Body'})
        self.assertEqual(payload['aps']['thread-id'], f'frienden:{self.space.pk}:{self.a.pk}')
        self.assertNotIn('secret-token', json.dumps(payload))
        self.assertNotIn('evil.invalid', json.dumps(payload))
        text.assert_called_once()

    @patch('User.models.NotificationDelivery._web_notification_text', return_value=('\u4f60' * 200, '\x00' * 10000))
    def test_actual_serialized_payload_fits_4096_bytes_without_damaging_unicode(self, text):
        payload = native_push_payload(self.event())
        encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.assertLessEqual(len(encoded), 4096)
        self.assertEqual(json.loads(encoded), payload)
        self.assertLessEqual(len(payload['aps']['alert']['title']), 128)
        self.assertLessEqual(len(payload['aps']['alert']['body']), 512)

    def test_missing_deleted_or_cross_space_recipient_is_rejected(self):
        event = self.event()
        for field, value in (('pk', None), ('user_id', 0), ('space_id', self.other_space.pk)):
            original = getattr(event, field)
            setattr(event, field, value)
            with self.assertRaises(ValueError):
                native_push_payload(event)
            setattr(event, field, original)
        event.user.is_deleted = True
        with self.assertRaises(ValueError):
            native_push_payload(event)
