from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat
from Friendship.models import Friendship
from Message.models import (
    Message,
    MessageEvent,
    MessageEventTypeChoice,
    MessageHistoryRecovery,
    MessageTypeChoice,
    MessageUserState,
)
from Message.validators import MessageErrors
from Space.models import Space
from User.models import User


class MessageHistoryRecoveryTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='History Recovery',
            slug='history',
            email='admin@example.com',
            admin_phone_verified_at=timezone.now(),
        )
        self.user = User.create(self.space, 'Member', email='member@example.com', verified=True)
        self.peer = User.create(self.space, 'Peer', email='peer@example.com', verified=True)
        Friendship.ensure_locked_friendship(self.user, self.peer)
        self.chat = Chat.get_or_create_direct(self.user, self.peer)

    def hide_message(self, content='Hidden message'):
        message = Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, content)
        message.hide_for(self.user)
        return message

    def test_verified_user_can_restore_hidden_history_once(self):
        message = self.hide_message()

        before = MessageHistoryRecovery.status_for(self.chat, self.user)
        self.assertEqual(before['limit'], 1)
        self.assertEqual(before['remaining'], 1)
        self.assertEqual(before['hidden_count'], 1)

        result = MessageHistoryRecovery.restore(self.chat, self.user)

        self.assertEqual(result['restored_count'], 1)
        self.assertEqual(result['remaining'], 0)
        self.assertFalse(MessageUserState.objects.filter(message=message, user=self.user).exists())
        self.assertTrue(Message.visible_for_user(self.chat, self.user).filter(id=message.id).exists())
        self.assertTrue(MessageEvent.objects.filter(
            message=message,
            target_user=self.user,
            type=MessageEventTypeChoice.RESTORED,
        ).exists())

        self.hide_message('Hidden again')
        with self.assertRaises(MessageErrors.HISTORY_RECOVERY_LIMIT_REACHED.__class__):
            MessageHistoryRecovery.restore(self.chat, self.user)

    def test_permanent_vip_receives_five_additional_recoveries(self):
        self.user.is_permanent_vip = True
        self.user.save(update_fields=['is_permanent_vip'])

        for index in range(6):
            self.hide_message(f'VIP hidden {index}')
            result = MessageHistoryRecovery.restore(self.chat, self.user)
            self.assertEqual(result['remaining'], 5 - index)

        self.hide_message('Seventh hidden')
        with self.assertRaises(MessageErrors.HISTORY_RECOVERY_LIMIT_REACHED.__class__):
            MessageHistoryRecovery.restore(self.chat, self.user)

    def test_unverified_user_cannot_restore_history(self):
        unverified = User.create(self.space, 'Basic')
        Friendship.ensure_locked_friendship(unverified, self.peer)
        chat = Chat.get_or_create_direct(unverified, self.peer)
        message = Message.create(chat, self.peer, MessageTypeChoice.TEXT, 'Private')
        message.hide_for(unverified)

        with self.assertRaises(MessageErrors.HISTORY_RECOVERY_VERIFICATION_REQUIRED.__class__):
            MessageHistoryRecovery.restore(chat, unverified)
        self.assertTrue(MessageUserState.objects.filter(message=message, user=unverified).exists())

    def test_recovery_does_not_revive_globally_recalled_message(self):
        recalled = self.hide_message('Recalled')
        visible = self.hide_message('Restorable')
        recalled.remove()

        status = MessageHistoryRecovery.status_for(self.chat, self.user)
        self.assertEqual(status['hidden_count'], 1)
        result = MessageHistoryRecovery.restore(self.chat, self.user)

        self.assertEqual(result['restored_count'], 1)
        self.assertTrue(MessageUserState.objects.filter(message=recalled, user=self.user).exists())
        self.assertFalse(Message.visible_for_user(self.chat, self.user).filter(id=recalled.id).exists())
        self.assertTrue(Message.visible_for_user(self.chat, self.user).filter(id=visible.id).exists())
