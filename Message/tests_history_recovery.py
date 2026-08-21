import json

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
from User.validators import UserErrors
from utils import auth


class MessageHistoryRecoveryTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='History Recovery',
            slug='history',
            email='admin@example.com',
            admin_phone_verified_at=timezone.now(),
        )
        self.user = User.create(self.space, 'Member', password='secret1', email='member@example.com', verified=True)
        self.peer = User.create(self.space, 'Peer', email='peer@example.com', verified=True)
        Friendship.ensure_locked_friendship(self.user, self.peer)
        self.chat = Chat.get_or_create_direct(self.user, self.peer)

    def hide_message(self, content='Hidden message'):
        message = Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, content)
        message.hide_for(self.user)
        return message

    def authorization(self):
        token = auth.get_login_token(self.user)['auth']
        return dict(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_restore_endpoint_accepts_password_and_clear_does_not_require_it(self):
        message = self.hide_message('Restore through API')
        response = self.client.post(
            '/messages/restore',
            data=json.dumps({'chat_id': self.chat.id, 'password': 'secret1'}),
            content_type='application/json',
            **self.authorization(),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(MessageUserState.objects.filter(message=message, user=self.user).exists())

        Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, 'Clear through API')
        clear_response = self.client.delete(
            '/messages/clear',
            data=json.dumps({'chat_id': self.chat.id}),
            content_type='application/json',
            **self.authorization(),
        )
        self.assertEqual(clear_response.status_code, 200, clear_response.content)

    def test_verified_user_can_restore_hidden_history_once(self):
        message = self.hide_message()

        before = MessageHistoryRecovery.status_for(self.chat, self.user)
        self.assertEqual(before['limit'], 1)
        self.assertEqual(before['remaining'], 1)
        self.assertEqual(before['hidden_count'], 1)

        result = MessageHistoryRecovery.restore(self.chat, self.user, 'secret1')

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
            MessageHistoryRecovery.restore(self.chat, self.user, 'secret1')

    def test_permanent_vip_receives_five_additional_recoveries(self):
        self.user.is_permanent_vip = True
        self.user.save(update_fields=['is_permanent_vip'])

        for index in range(6):
            self.hide_message(f'VIP hidden {index}')
            result = MessageHistoryRecovery.restore(self.chat, self.user, 'secret1')
            self.assertEqual(result['remaining'], 5 - index)

        self.hide_message('Seventh hidden')
        with self.assertRaises(MessageErrors.HISTORY_RECOVERY_LIMIT_REACHED.__class__):
            MessageHistoryRecovery.restore(self.chat, self.user, 'secret1')

    def test_unverified_user_cannot_restore_history(self):
        unverified = User.create(self.space, 'Basic', password='secret2')
        Friendship.ensure_locked_friendship(unverified, self.peer)
        chat = Chat.get_or_create_direct(unverified, self.peer)
        message = Message.create(chat, self.peer, MessageTypeChoice.TEXT, 'Private')
        message.hide_for(unverified)

        with self.assertRaises(MessageErrors.HISTORY_RECOVERY_VERIFICATION_REQUIRED.__class__):
            MessageHistoryRecovery.restore(chat, unverified, 'secret2')
        self.assertTrue(MessageUserState.objects.filter(message=message, user=unverified).exists())

    def test_password_is_required_for_every_recovery(self):
        message = self.hide_message('Protected')

        with self.assertRaises(UserErrors.PASSWORD_ERROR.__class__) as wrong_password:
            MessageHistoryRecovery.restore(self.chat, self.user, 'wrong-password')
        self.assertEqual(wrong_password.exception.identifier, UserErrors.PASSWORD_ERROR.identifier)
        self.assertTrue(MessageUserState.objects.filter(message=message, user=self.user).exists())
        self.assertFalse(MessageHistoryRecovery.objects.filter(user=self.user).exists())

        no_password = User.create(self.space, 'NoPassword', email='none@example.com', verified=True)
        Friendship.ensure_locked_friendship(no_password, self.peer)
        no_password_chat = Chat.get_or_create_direct(no_password, self.peer)
        no_password_message = Message.create(no_password_chat, self.peer, MessageTypeChoice.TEXT, 'No password')
        no_password_message.hide_for(no_password)
        with self.assertRaises(UserErrors.PASSWORD_NOT_SET.__class__) as password_not_set:
            MessageHistoryRecovery.restore(no_password_chat, no_password, '')
        self.assertEqual(password_not_set.exception.identifier, UserErrors.PASSWORD_NOT_SET.identifier)
        self.assertTrue(MessageUserState.objects.filter(message=no_password_message, user=no_password).exists())

    def test_recovery_does_not_revive_globally_recalled_message(self):
        recalled = self.hide_message('Recalled')
        visible = self.hide_message('Restorable')
        recalled.remove()

        status = MessageHistoryRecovery.status_for(self.chat, self.user)
        self.assertEqual(status['hidden_count'], 1)
        result = MessageHistoryRecovery.restore(self.chat, self.user, 'secret1')

        self.assertEqual(result['restored_count'], 1)
        self.assertTrue(MessageUserState.objects.filter(message=recalled, user=self.user).exists())
        self.assertFalse(Message.visible_for_user(self.chat, self.user).filter(id=recalled.id).exists())
        self.assertTrue(Message.visible_for_user(self.chat, self.user).filter(id=visible.id).exists())
