import json
from unittest.mock import patch

from django.test import TestCase

from Chat.models import Chat
from Message.models import Message
from Space.models import Space
from User.models import NotificationPreference, User, UserNotificationChoice
from utils import auth


class SpaceAdminApiTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Test Space',
            slug='test-space',
            email='admin@example.com',
        )
        self.official = self.space.ensure_official_user()
        self.member = User.create(
            space=self.space,
            name='Member',
            email='member@example.com',
            verified=True,
        )
        self.token = auth.get_space_login_token(self.space)['auth']

    def authorization(self):
        return dict(HTTP_AUTHORIZATION=f'Bearer {self.token}')

    @patch('User.models.NotificationEvent._enqueue_deliveries_after_commit')
    def test_broadcast_is_idempotent(self, enqueue):
        payload = dict(content='Hello everyone', broadcast_id='broadcast:test')

        first = self.client.post(
            '/spaces/admin/broadcast',
            data=json.dumps(payload),
            content_type='application/json',
            **self.authorization(),
        )
        second = self.client.post(
            '/spaces/admin/broadcast',
            data=json.dumps(payload),
            content_type='application/json',
            **self.authorization(),
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        chat = Chat.get_or_create_direct(self.official, self.member)
        self.assertEqual(
            Message.objects.filter(
                chat=chat,
                user=self.official,
                client_message_id=payload['broadcast_id'],
            ).count(),
            1,
        )
        self.assertEqual(second.json()['body']['duplicate_count'], 1)
        enqueue.assert_called()

    def test_member_list_only_exposes_contact_status(self):
        NotificationPreference.set_preference(
            self.member,
            UserNotificationChoice.EMAIL,
            enabled=1,
            offline_threshold_minutes=12,
        )

        response = self.client.get('/spaces/admin/users', **self.authorization())

        self.assertEqual(response.status_code, 200)
        member = response.json()['body'][0]
        self.assertNotIn('email', member)
        self.assertTrue(member['contacts']['email']['bound'])
        self.assertTrue(member['contacts']['email']['verified'])
        email_pref = next(
            item for item in member['notification_preferences']
            if item['channel'] == UserNotificationChoice.EMAIL
        )
        self.assertTrue(email_pref['enabled'])
        self.assertEqual(email_pref['offline_threshold_minutes'], 12)
