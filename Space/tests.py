import json
from unittest.mock import patch

from django.test import TestCase

from Chat.models import Chat
from Config.models import Config, ConfigInstance
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from User.models import GrowthEvent, NotificationPreference, User, UserNotificationChoice
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
        Config.objects.create(
            key=ConfigInstance.QINIU_DOMAIN,
            value='https://resource.example.com',
        )
        self.token = auth.get_space_login_token(self.space)['auth']

    def authorization(self):
        return dict(HTTP_AUTHORIZATION=f'Bearer {self.token}')

    def user_authorization(self, user):
        token = auth.get_login_token(user)['auth']
        return dict(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_official_user_can_exchange_admin_session(self):
        response = self.client.post(
            '/spaces/admin/session',
            **self.user_authorization(self.official),
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()['body']
        self.assertEqual(payload['space']['space_id'], self.space.id)
        self.assertIn('auth', payload['auth'])

    def test_member_cannot_exchange_admin_session(self):
        response = self.client.post(
            '/spaces/admin/session',
            **self.user_authorization(self.member),
        )

        self.assertEqual(response.status_code, 403, response.content)

    @patch('User.models.NotificationEvent._enqueue_deliveries_after_commit')
    def test_broadcast_is_idempotent(self, enqueue):
        payload = dict(content='Hello everyone', type=0, broadcast_id='broadcast:test')

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

    @patch('User.models.NotificationEvent._enqueue_deliveries_after_commit')
    def test_broadcast_supports_media_messages(self, enqueue):
        payload = dict(
            content=json.dumps({
                'key': 'sermo/messages/image/test.jpg',
                'mime_type': 'image/jpeg',
            }),
            type=1,
            broadcast_id='broadcast:image:test',
        )

        response = self.client.post(
            '/spaces/admin/broadcast',
            data=json.dumps(payload),
            content_type='application/json',
            **self.authorization(),
        )

        self.assertEqual(response.status_code, 200, response.content)
        chat = Chat.get_or_create_direct(self.official, self.member)
        message = Message.objects.get(
            chat=chat,
            user=self.official,
            client_message_id=payload['broadcast_id'],
        )
        self.assertEqual(message.type, 1)
        self.assertIsNotNone(message.blob_slug)
        self.assertEqual(message.preview_text(), '[图片]')
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

    def test_admin_can_name_space_growth_levels(self):
        level_names = [f'阶段{index}' for index in range(1, 19)]
        response = self.client.post(
            '/spaces/admin/settings',
            data=json.dumps({
                'name': self.space.name,
                'group_square_enabled': 1,
                'member_limit': 100,
                'level_names': level_names,
            }),
            content_type='application/json',
            **self.authorization(),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.space.refresh_from_db()
        self.assertEqual(self.space.level_names, level_names)
        self.assertEqual(response.json()['body']['level_names'], level_names)

    def test_official_account_has_highest_growth_level(self):
        self.space.level_names = [f'阶段{index}' for index in range(1, 19)]
        self.space.save(update_fields=['level_names'])

        growth = self.official.calculate_growth()

        self.assertEqual(growth['score'], 6550)
        self.assertEqual(growth['level'], 18)
        self.assertEqual(growth['name'], '阶段18')
        self.assertIn('广场光环', growth['privileges'])

    def test_daily_chat_growth_is_capped(self):
        chat = Chat.get_or_create_direct(self.official, self.member)
        for index in range(20):
            Message.create(
                chat=chat,
                user=self.member,
                message_type=MessageTypeChoice.TEXT,
                content=f'message {index}',
                client_message_id=f'growth-{index}',
            )

        self.member.refresh_from_db()
        growth = self.member.calculate_growth()
        self.assertEqual(growth['daily_chat']['earned'], 20)
        self.assertEqual(self.member.growth_score, 65)
        self.assertTrue(
            next(item for item in growth['milestones'] if item['key'] == 'security:email')['earned']
        )

    def test_one_time_growth_is_idempotent_and_repairs_inflated_points(self):
        self.member.calculate_growth()
        self.member.calculate_growth()
        email_event = GrowthEvent.objects.get(user=self.member, event_key='security:email')
        self.assertEqual(email_event.points, 45)

        email_event.points = 450
        email_event.save(update_fields=['points'])
        self.member.growth_score = 450
        self.member.save(update_fields=['growth_score'])

        growth = self.member.calculate_growth()
        email_event.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(email_event.points, 45)
        self.assertEqual(growth['score'], 45)
        self.assertEqual(self.member.growth_score, 45)
