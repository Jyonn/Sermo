import json

from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat, ChatMember, ChatMemberRoleChoice, ChatMemberStatusChoice, ChatTypeChoice, ChatUserPreference
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from User.models import NotificationEvent, User
from utils import auth


class ChatNotificationPreferenceTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Mute Test', slug='mute-test', email='admin@example.com')
        self.sender = User.create(self.space, 'Sender', verified=True)
        self.recipient = User.create(self.space, 'Recipient', verified=True)
        self.chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.GROUP,
            title='Muted group',
            created_by=self.sender,
        )
        ChatMember.objects.create(
            chat=self.chat,
            user=self.sender,
            role=ChatMemberRoleChoice.OWNER,
            status=ChatMemberStatusChoice.ACTIVE,
            joined_at=timezone.now(),
        )
        ChatMember.objects.create(
            chat=self.chat,
            user=self.recipient,
            status=ChatMemberStatusChoice.ACTIVE,
            joined_at=timezone.now(),
        )

    def authorization(self, user):
        return {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(user)['auth']}"}

    def test_muted_group_suppresses_external_notification_but_keeps_unread(self):
        preference_response = self.client.post(
            f'/chats/preference?chat_id={self.chat.id}',
            data=json.dumps({'notifications_muted': 1}),
            content_type='application/json',
            **self.authorization(self.recipient),
        )
        self.assertEqual(preference_response.status_code, 200, preference_response.content)
        self.assertTrue(preference_response.json()['body']['notifications_muted'])

        message = Message.create(self.chat, self.sender, MessageTypeChoice.TEXT, 'still delivered')
        events = NotificationEvent.emit_message_notifications(message, actor=self.sender, enqueue=False)
        self.assertFalse(any(event.user_id == self.recipient.id for event in events))

        chat_list = self.client.get('/chats/', **self.authorization(self.recipient))
        self.assertEqual(chat_list.status_code, 200, chat_list.content)
        payload = next(item for item in chat_list.json()['body'] if item['chat_id'] == self.chat.id)
        self.assertEqual(payload['unread_count'], 1)
        self.assertTrue(payload['notifications_muted'])
        self.assertFalse(payload['unread_badge_muted'])
        self.assertEqual(payload['last_message']['content'], 'still delivered')

    def test_weak_unread_keeps_count_but_marks_badge_as_muted(self):
        ChatUserPreference.update(self.chat, self.recipient, unread_badge_muted=True)
        Message.create(self.chat, self.sender, MessageTypeChoice.TEXT, 'quiet unread')

        chat_list = self.client.get('/chats/', **self.authorization(self.recipient))
        payload = next(item for item in chat_list.json()['body'] if item['chat_id'] == self.chat.id)

        self.assertEqual(payload['unread_count'], 1)
        self.assertTrue(payload['notifications_muted'])
        self.assertTrue(payload['unread_badge_muted'])

    def test_unmuted_group_keeps_normal_notification_behavior(self):
        message = Message.create(self.chat, self.sender, MessageTypeChoice.TEXT, 'notify me')
        events = NotificationEvent.emit_message_notifications(message, actor=self.sender, enqueue=False)
        self.assertTrue(any(event.user_id == self.recipient.id for event in events))
        self.assertFalse(ChatUserPreference.ensure(self.chat, self.recipient).notifications_muted)
