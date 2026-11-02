import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from User import tests_native_push_bindings as fixtures
from User.models import NotificationEvent, NotificationEventTypeChoice, NotificationTopicPreference as Pref
from User.native_push_delivery import NativePushDelivery as Delivery
from User.native_push_dispatch import dispatch_native_delivery
from User.native_push_models import NativePushBinding as Binding
from utils.apns import APNsResult, APNsUnavailable


class NativePushDispatchTests(TestCase):
    setUp = fixtures.NativePushBindingTests.setUp
    prefer = fixtures.NativePushBindingTests.prefer
    refresh = fixtures.NativePushBindingTests.refresh

    def setup_delivery(self, user=None):
        self.binding = self.prefer(user)
        user = user or self.a
        event = NotificationEvent.objects.create(user=user, space=user.space,
            event_type=NotificationEventTypeChoice.SYSTEM, payload={'kind': 'research_task'})
        row = Delivery.enqueue(event, environments=('sandbox',))[0]
        self.transport = Mock(configuration=SimpleNamespace(environments=('sandbox',)))
        self.transport.send.return_value = APNsResult('accepted')
        return row

    def test_dispatch_submits_once_with_canonical_payload_and_claim_identity(self):
        row = self.setup_delivery()
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'sent')
        self.assertIsNone(dispatch_native_delivery(row.pk, self.transport))
        self.transport.send.assert_called_once()
        row.refresh_from_db()
        values = self.transport.send.call_args.kwargs
        self.assertEqual(values['notification_id'], row.claim_id)
        self.assertEqual(values['token'], self.token)
        self.assertEqual(values['payload']['frienden']['account_id'], self.a.pk)
        self.assertEqual(row.state, 'sent')

    def test_no_provider_environment_or_opt_out_means_no_submission(self):
        row = self.setup_delivery()
        self.transport.configuration.environments = ()
        self.assertIsNone(dispatch_native_delivery(row.pk, self.transport))
        self.transport.configuration.environments = ('sandbox',)
        self.prefer(enabled=False, revision=self.binding.revision)
        self.assertIsNone(dispatch_native_delivery(row.pk, self.transport))
        self.transport.send.assert_not_called()

    def test_opt_out_or_topic_change_after_claim_is_rechecked(self):
        for disable in ('binding', 'topic'):
            row = self.setup_delivery()
            original = Delivery.claim
            def claim(*args, **kwargs):
                result = original(*args, **kwargs)
                if disable == 'binding':
                    self.prefer(enabled=False, revision=self.binding.revision)
                else:
                    Pref.set_enabled(self.a, 0, row.event.topic(), 0, False)
                return result
            with patch.object(Delivery, 'claim', side_effect=claim):
                self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'skipped')
            self.transport.send.assert_not_called()
            row.refresh_from_db()
            self.assertEqual(row.state, 'skipped')
            Binding.objects.all().delete()
            Pref.objects.all().delete()

    def test_unknown_outcome_is_not_retried(self):
        row = self.setup_delivery()
        self.transport.send.return_value = APNsResult('unknown')
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'unknown')
        self.assertIsNone(dispatch_native_delivery(row.pk, self.transport))
        self.transport.send.assert_called_once()

    def test_expired_claim_cannot_be_reported_as_success_or_invalidate_token(self):
        row = self.setup_delivery()
        def accepted(**kwargs):
            Delivery.objects.filter(pk=row.pk).update(claimed_at=timezone.now() - datetime.timedelta(minutes=16))
            Delivery.expire_claims()
            return APNsResult('accepted')
        self.transport.send.side_effect = accepted
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'unknown')
        row.refresh_from_db()
        self.assertEqual((row.state, row.reason), ('unknown', 'claim_expired'))

    def test_token_invalidation_is_account_scoped(self):
        row = self.setup_delivery()
        other = self.prefer(self.b)
        self.transport.send.return_value = APNsResult('rejected', int(timezone.now().timestamp() * 1000) + 1)
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'failed')
        self.binding.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNotNone(self.binding.invalidated_at)
        self.assertIsNone(other.invalidated_at)

    def test_stale_and_future_provider_timestamps_do_not_invalidate_current_registration(self):
        for timestamp in (1, int(timezone.now().timestamp() * 1000) + 600000):
            row = self.setup_delivery()
            self.transport.send.return_value = APNsResult('rejected', timestamp)
            dispatch_native_delivery(row.pk, self.transport)
            self.binding.refresh_from_db()
            self.assertIsNone(self.binding.invalidated_at)
            Binding.objects.all().delete()

    def test_token_rotation_while_request_is_in_flight_wins_over_old_rejection(self):
        row = self.setup_delivery()
        def reject(**kwargs):
            self.refresh(self.binding.revision, 'cd' * 32)
            return APNsResult('rejected', int(timezone.now().timestamp() * 1000) + 1)
        self.transport.send.side_effect = reject
        dispatch_native_delivery(row.pk, self.transport)
        self.binding.refresh_from_db()
        self.assertEqual(self.binding.token, 'cd' * 32)
        self.assertIsNone(self.binding.invalidated_at)

    def test_local_errors_never_persist_secret_exception_text(self):
        for error, expected in ((APNsUnavailable('secret-key'), 'failed'),
                                (ValueError('secret-token'), 'failed'), (RuntimeError('secret-body'), 'unknown')):
            row = self.setup_delivery()
            self.transport.send.side_effect = error
            self.assertEqual(dispatch_native_delivery(row.pk, self.transport), expected)
            row.refresh_from_db()
            self.assertEqual(row.reason, '')
            self.assertEqual(row.state, expected)
            Binding.objects.all().delete()

    @patch('User.native_push_dispatch.native_push_payload', side_effect=ValueError('secret-content'))
    def test_payload_failure_never_submits(self, payload):
        row = self.setup_delivery()
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'failed')
        self.transport.send.assert_not_called()

    def chat_delivery(self, group=False):
        from Chat.models import Chat, ChatMember, ChatMemberStatusChoice
        from Message.models import Message
        from User.models import User

        row = self.setup_delivery()
        sender = User.create(self.space, 'Sender', verified=True)
        chat = Chat.objects.create(space=self.space, created_by=sender, chat_type=1 if group else 0)
        member = ChatMember.objects.create(chat=chat, user=self.a, status=ChatMemberStatusChoice.ACTIVE)
        message = Message.objects.create(chat=chat, user=sender, type=0, content='Hello')
        row.event.event_type = 2 if group else 1
        row.event.payload = dict(chat_id=chat.pk, message_id=message.pk, content='Hello')
        row.event.save(update_fields=['event_type', 'payload'])
        return row, member, message

    def test_visible_chat_message_is_submitted(self):
        row, member, message = self.chat_delivery()
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'sent')
        self.transport.send.assert_called_once()

    def test_recalled_or_hidden_messages_never_leave_server(self):
        from Message.models import MessageUserState

        row, member, message = self.chat_delivery()
        message.is_deleted = True
        message.save(update_fields=['is_deleted'])
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'skipped')
        self.transport.send.assert_not_called()
        message.is_deleted = False
        message.save(update_fields=['is_deleted'])
        MessageUserState.objects.create(message=message, user=self.a)
        row = Delivery.enqueue(NotificationEvent.objects.create(user=self.a, space=self.space, event_type=1,
                                    payload=dict(chat_id=message.chat_id, message_id=message.pk)), environments=('sandbox',))[0]
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'skipped')
        self.transport.send.assert_not_called()

    def test_left_chat_or_muted_group_never_submits(self):
        from Chat.models import ChatMemberStatusChoice, ChatUserPreference

        row, member, message = self.chat_delivery(group=True)
        ChatUserPreference.objects.create(chat=message.chat, user=self.a, notifications_muted=True)
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'skipped')
        self.transport.send.assert_not_called()
        ChatUserPreference.objects.filter(chat=message.chat, user=self.a).delete()
        member.status = ChatMemberStatusChoice.LEFT
        member.save(update_fields=['status'])
        row = Delivery.enqueue(NotificationEvent.objects.create(user=self.a, space=self.space, event_type=2,
                                    payload=dict(chat_id=message.chat_id, message_id=message.pk)), environments=('sandbox',))[0]
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'skipped')
        self.transport.send.assert_not_called()
