from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatReadState, ChatTypeChoice, ChatUserPreference
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from User.models import (
    NotificationChannelCursor,
    NotificationDelivery,
    NotificationEvent,
    NotificationPreference,
    User,
    UserNotificationChoice,
)


class NotificationDigestTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Digest', slug='digest', email='admin@example.com')
        self.sender = User.create(self.space, 'Sender', verified=True)
        self.recipient = User.create(self.space, 'Recipient', verified=True)
        self.recipient.email = 'recipient@example.com'
        self.recipient.email_verified_at = timezone.now()
        self.recipient.last_heartbeat = timezone.now() - timedelta(hours=2)
        self.recipient.save(update_fields=['email', 'email_verified_at', 'last_heartbeat'])
        self.chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.GROUP,
            title='Digest chat',
            created_by=self.sender,
        )
        joined_at = timezone.now() - timedelta(minutes=1)
        ChatMember.objects.create(chat=self.chat, user=self.sender, status=ChatMemberStatusChoice.ACTIVE, joined_at=joined_at)
        ChatMember.objects.create(chat=self.chat, user=self.recipient, status=ChatMemberStatusChoice.ACTIVE, joined_at=joined_at)
        self.pref = NotificationPreference.set_preference(
            self.recipient,
            UserNotificationChoice.EMAIL,
            enabled=True,
            offline_threshold_minutes=30,
        )
        NotificationChannelCursor.objects.filter(user=self.recipient, channel=UserNotificationChoice.EMAIL).update(
            last_message_id=Message.objects.order_by('-id').values_list('id', flat=True).first() or 0,
        )

    def create_notified_message(self, text):
        message = Message.create(self.chat, self.sender, MessageTypeChoice.TEXT, text)
        NotificationEvent.emit_message_notifications(message, actor=self.sender, enqueue=False)
        return message

    @patch('User.models.notificator.mail', return_value={'request_id': 'digest-1'})
    def test_due_messages_are_merged_and_cursor_advances(self, mail):
        first = self.create_notified_message('first')
        second = self.create_notified_message('second')

        summary = NotificationChannelCursor.process_due()

        self.assertEqual(mail.call_count, 1)
        self.assertEqual(NotificationDelivery.objects.filter(status=1).count(), 2)
        cursor = NotificationChannelCursor.objects.get(user=self.recipient, channel=UserNotificationChoice.EMAIL)
        self.assertEqual(cursor.last_message_id, second.id)
        self.assertEqual(summary['sent'], 2)
        self.assertLess(first.id, cursor.last_message_id)

    @patch('User.models.notificator.mail')
    def test_read_messages_are_skipped_without_delivery(self, mail):
        message = self.create_notified_message('already read')
        ChatReadState.mark_read(self.chat, self.recipient)

        NotificationChannelCursor.process_due()

        mail.assert_not_called()
        self.assertFalse(NotificationDelivery.objects.exists())
        cursor = NotificationChannelCursor.objects.get(user=self.recipient, channel=UserNotificationChoice.EMAIL)
        self.assertEqual(cursor.last_message_id, message.id)

    @patch('User.models.notificator.mail')
    def test_group_muting_is_rechecked_before_delivery(self, mail):
        message = self.create_notified_message('muted later')
        ChatUserPreference.update(self.chat, self.recipient, notifications_muted=True)

        NotificationChannelCursor.process_due()

        mail.assert_not_called()
        cursor = NotificationChannelCursor.objects.get(user=self.recipient, channel=UserNotificationChoice.EMAIL)
        self.assertEqual(cursor.last_message_id, message.id)
