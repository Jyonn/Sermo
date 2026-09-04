from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatReadState, ChatTypeChoice, ChatUserPreference
from Friendship.models import Friendship
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from User.models import (
    NotificationChannelCursor,
    NotificationDelivery,
    NotificationDeliveryStatusChoice,
    NotificationEvent,
    NotificationEventTypeChoice,
    NotificationPreference,
    InstantNotificationEndpoint,
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

    def create_notified_message(self, text, chat=None, sender=None):
        chat = chat or self.chat
        sender = sender or self.sender
        message = Message.create(chat, sender, MessageTypeChoice.TEXT, text)
        NotificationEvent.emit_message_notifications(message, actor=sender, enqueue=False)
        return message

    def create_direct_chat(self, sender):
        Friendship.ensure_locked_friendship(sender, self.recipient)
        return Chat.get_or_create_direct(sender, self.recipient)

    def create_group_chat(self, title):
        chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.GROUP,
            title=title,
            created_by=self.sender,
        )
        joined_at = timezone.now() - timedelta(minutes=1)
        ChatMember.objects.create(chat=chat, user=self.sender, status=ChatMemberStatusChoice.ACTIVE, joined_at=joined_at)
        ChatMember.objects.create(chat=chat, user=self.recipient, status=ChatMemberStatusChoice.ACTIVE, joined_at=joined_at)
        return chat

    def enable_bark(self):
        InstantNotificationEndpoint.objects.create(
            user=self.recipient,
            provider='bark',
            target='https://api.day.app/test-device',
            verified_at=timezone.now(),
        )
        return NotificationPreference.set_preference(
            self.recipient,
            UserNotificationChoice.BARK,
            enabled=True,
            offline_threshold_minutes=60,
        )

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

    @patch('User.models.notificator.mail', return_value={'request_id': 'digest-paced'})
    def test_recent_email_attempt_defers_the_next_digest(self, mail):
        first = self.create_notified_message('first')
        NotificationChannelCursor.process_due()
        second = self.create_notified_message('second')

        NotificationChannelCursor.process_due()

        self.assertEqual(mail.call_count, 1)
        cursor = NotificationChannelCursor.objects.get(user=self.recipient, channel=UserNotificationChoice.EMAIL)
        self.assertEqual(cursor.last_message_id, first.id)

        NotificationDelivery.objects.filter(channel=UserNotificationChoice.EMAIL).update(
            attempted_at=timezone.now() - timedelta(minutes=31),
        )
        NotificationChannelCursor.process_due()

        self.assertEqual(mail.call_count, 2)
        cursor.refresh_from_db()
        self.assertEqual(cursor.last_message_id, second.id)

    @patch('User.models.notificator.mail', side_effect=RuntimeError('outcome uncertain'))
    def test_failed_digest_is_not_automatically_resent(self, mail):
        message = self.create_notified_message('send once')

        NotificationChannelCursor.process_due()
        cursor = NotificationChannelCursor.objects.get(user=self.recipient, channel=UserNotificationChoice.EMAIL)
        self.assertEqual(cursor.last_message_id, message.id)
        NotificationDelivery.objects.filter(channel=UserNotificationChoice.EMAIL).update(
            attempted_at=timezone.now() - timedelta(minutes=31),
        )
        NotificationChannelCursor.process_due()

        self.assertEqual(mail.call_count, 1)
        delivery = NotificationDelivery.objects.get(channel=UserNotificationChoice.EMAIL)
        self.assertEqual(delivery.status, NotificationDeliveryStatusChoice.FAILED)
        cursor.refresh_from_db()
        self.assertEqual(cursor.last_message_id, message.id)

    def test_event_delivery_route_is_idempotent(self):
        event = NotificationEvent.objects.create(
            space=self.space,
            user=self.recipient,
            event_type=NotificationEventTypeChoice.SYSTEM,
            payload={'kind': 'friend_request'},
        )

        NotificationDelivery.enqueue_for_event(event)
        NotificationDelivery.enqueue_for_event(event)

        self.assertEqual(NotificationDelivery.objects.filter(
            event=event,
            channel=UserNotificationChoice.EMAIL,
        ).count(), 1)

    def test_pending_delivery_can_only_be_claimed_once(self):
        event = NotificationEvent.objects.create(
            space=self.space,
            user=self.recipient,
            event_type=NotificationEventTypeChoice.SYSTEM,
            payload={'kind': 'friend_request'},
        )
        delivery, _created = NotificationDelivery.get_or_create_for_route(
            event,
            UserNotificationChoice.EMAIL,
        )

        first_claim = NotificationDelivery._claim_pending([delivery])
        second_claim = NotificationDelivery._claim_pending([delivery])

        self.assertEqual([item.id for item in first_claim], [delivery.id])
        self.assertEqual(second_claim, [])

    @patch('User.models.notificator.mail', return_value={'request_id': 'cursor-fair'})
    def test_digest_worker_prioritizes_the_oldest_cursor(self, mail):
        other = User.create(self.space, 'Older cursor', verified=True)
        other.email = 'older@example.com'
        other.email_verified_at = timezone.now()
        other.last_heartbeat = timezone.now() - timedelta(hours=2)
        other.save(update_fields=['email', 'email_verified_at', 'last_heartbeat'])
        ChatMember.objects.create(
            chat=self.chat,
            user=other,
            status=ChatMemberStatusChoice.ACTIVE,
            joined_at=timezone.now() - timedelta(minutes=1),
        )
        NotificationPreference.set_preference(
            other,
            UserNotificationChoice.EMAIL,
            enabled=True,
            offline_threshold_minutes=30,
        )
        current_message_id = Message.objects.order_by('-id').values_list('id', flat=True).first() or 0
        other_cursor, _created = NotificationChannelCursor.objects.update_or_create(
            user=other,
            channel=UserNotificationChoice.EMAIL,
            defaults={'last_message_id': current_message_id},
        )
        own_cursor = NotificationChannelCursor.objects.get(
            user=self.recipient,
            channel=UserNotificationChoice.EMAIL,
        )
        NotificationChannelCursor.objects.filter(id=other_cursor.id).update(
            updated_at=timezone.now() - timedelta(days=1),
        )
        NotificationChannelCursor.objects.filter(id=own_cursor.id).update(updated_at=timezone.now())
        message = self.create_notified_message('fair delivery')

        NotificationChannelCursor.process_due(limit_users=1)

        self.assertEqual(mail.call_count, 1)
        self.assertEqual(mail.call_args.kwargs['recipient_name'], other.name)
        other_cursor.refresh_from_db()
        own_cursor.refresh_from_db()
        self.assertEqual(other_cursor.last_message_id, message.id)
        self.assertLess(own_cursor.last_message_id, message.id)

    @patch('User.models.notificator.mail', return_value={'request_id': 'pending-paced'})
    def test_pending_worker_sends_at_most_one_email_per_user(self, mail):
        for kind in ('friend_request', 'friend_request_accepted'):
            event = NotificationEvent.objects.create(
                space=self.space,
                user=self.recipient,
                event_type=NotificationEventTypeChoice.SYSTEM,
                payload={'kind': kind},
            )
            NotificationDelivery.enqueue_for_event(event)

        NotificationDelivery.process_pending()

        self.assertEqual(mail.call_count, 1)
        self.assertEqual(NotificationDelivery.objects.filter(
            channel=UserNotificationChoice.EMAIL,
            status=NotificationDeliveryStatusChoice.SENT,
        ).count(), 1)
        self.assertEqual(NotificationDelivery.objects.filter(
            channel=UserNotificationChoice.EMAIL,
            status=NotificationDeliveryStatusChoice.PENDING,
        ).count(), 1)

    @patch('User.models.notificator.mail', return_value={'request_id': 'pending-fair'})
    def test_pending_worker_does_not_let_one_user_fill_the_batch(self, mail):
        other = User.create(self.space, 'Other recipient', verified=True)
        other.email = 'other@example.com'
        other.email_verified_at = timezone.now()
        other.last_heartbeat = timezone.now() - timedelta(hours=2)
        other.save(update_fields=['email', 'email_verified_at', 'last_heartbeat'])
        NotificationPreference.set_preference(
            other,
            UserNotificationChoice.EMAIL,
            enabled=True,
            offline_threshold_minutes=30,
        )
        for recipient in (self.recipient, self.recipient, other):
            event = NotificationEvent.objects.create(
                space=self.space,
                user=recipient,
                event_type=NotificationEventTypeChoice.SYSTEM,
                payload={'kind': 'friend_request'},
            )
            NotificationDelivery.enqueue_for_event(event)

        NotificationDelivery.process_pending(limit=2)

        self.assertEqual(mail.call_count, 2)
        sent_user_ids = set(NotificationDelivery.objects.filter(
            channel=UserNotificationChoice.EMAIL,
            status=NotificationDeliveryStatusChoice.SENT,
        ).values_list('event__user_id', flat=True))
        self.assertEqual(sent_user_ids, {self.recipient.id, other.id})

    @patch('User.models.notificator.bark')
    def test_bark_is_not_processed_by_digest_cursor(self, bark):
        self.enable_bark()
        self.create_notified_message('instant only')

        NotificationChannelCursor.process_due()

        bark.assert_not_called()
        self.assertFalse(NotificationDelivery.objects.filter(channel=UserNotificationChoice.BARK).exists())

    @patch('User.models.notificator.ntfy')
    def test_ntfy_only_receiver_is_sent_without_legacy_bark_fields(self, ntfy):
        endpoint = InstantNotificationEndpoint.objects.create(
            user=self.recipient,
            provider='ntfy',
            target='https://ntfy.sh/sermo-test',
            verified_at=timezone.now(),
        )
        endpoint.set_enabled(True)
        message = self.create_notified_message('ntfy only')
        event = NotificationEvent.objects.get(user=self.recipient, payload__message_id=message.id)

        deliveries = NotificationDelivery.enqueue_instant_for_event(event)

        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0].instant_endpoint_id, endpoint.id)
        ntfy.assert_called_once()

    def test_endpoint_switch_keeps_general_instant_preference_in_sync(self):
        endpoint = InstantNotificationEndpoint.objects.create(
            user=self.recipient,
            provider='ntfy',
            target='https://ntfy.sh/sermo-test',
            verified_at=timezone.now(),
        )

        endpoint.set_enabled(True)
        self.assertTrue(NotificationPreference.objects.get(
            user=self.recipient,
            channel=UserNotificationChoice.BARK,
        ).enabled)

        endpoint.set_enabled(False)
        self.assertFalse(NotificationPreference.objects.get(
            user=self.recipient,
            channel=UserNotificationChoice.BARK,
        ).enabled)

    @patch('User.models.notificator.bark')
    def test_offline_bark_is_sent_immediately_per_message(self, bark):
        self.enable_bark()
        first = self.create_notified_message('first instant')
        second = self.create_notified_message('second instant')
        events = list(NotificationEvent.objects.filter(
            user=self.recipient,
            payload__message_id__in=[first.id, second.id],
        ).order_by('id'))

        for event in events:
            NotificationDelivery.enqueue_instant_for_event(event)

        self.assertEqual(bark.call_count, 2)
        self.assertEqual(
            NotificationDelivery.objects.filter(
                channel=UserNotificationChoice.BARK,
                status=1,
            ).count(),
            2,
        )

    @patch('User.models.notificator.bark')
    def test_online_user_does_not_queue_bark_for_later(self, bark):
        self.enable_bark()
        self.recipient.last_heartbeat = timezone.now()
        self.recipient.save(update_fields=['last_heartbeat'])
        message = self.create_notified_message('seen while online')
        event = NotificationEvent.objects.get(user=self.recipient, payload__message_id=message.id)

        deliveries = NotificationDelivery.enqueue_instant_for_event(event)

        self.assertEqual(deliveries, [])
        bark.assert_not_called()
        self.assertFalse(NotificationDelivery.objects.filter(channel=UserNotificationChoice.BARK).exists())

    @patch('User.models.notificator.pushdeer', create=True)
    @patch('User.models.notificator.gotify', create=True)
    @patch('User.models.notificator.ntfy', create=True)
    @patch('User.models.notificator.bark')
    def test_all_enabled_instant_receivers_are_sent_independently(self, bark, ntfy, gotify, pushdeer):
        self.enable_bark()
        InstantNotificationEndpoint.objects.create(
            user=self.recipient,
            provider='ntfy',
            target='https://ntfy.sh/sermo-test',
            verified_at=timezone.now(),
        )
        InstantNotificationEndpoint.objects.create(
            user=self.recipient,
            provider='gotify',
            target='https://push.example.com',
            secret='app-token',
            verified_at=timezone.now(),
        )
        InstantNotificationEndpoint.objects.create(
            user=self.recipient,
            provider='pushdeer',
            target='PDU_sermo-test-pushkey',
            verified_at=timezone.now(),
        )
        message = self.create_notified_message('multi receiver')
        event = NotificationEvent.objects.get(user=self.recipient, payload__message_id=message.id)

        deliveries = NotificationDelivery.enqueue_instant_for_event(event)

        self.assertEqual(len(deliveries), 4)
        bark.assert_called_once()
        ntfy.assert_called_once()
        gotify.assert_called_once()
        pushdeer.assert_called_once()

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

    @patch('User.models.notificator.mail', return_value={'request_id': 'digest-order'})
    def test_digest_prioritizes_pinned_then_direct_and_limits_each_chat(self, mail):
        self.recipient.language = 'en'
        self.recipient.save(update_fields=['language'])
        self.create_notified_message('ordinary-group-omitted')
        direct_sender_a = User.create(self.space, 'Direct A', verified=True)
        direct_sender_b = User.create(self.space, 'Direct B', verified=True)
        direct_a = self.create_direct_chat(direct_sender_a)
        direct_b = self.create_direct_chat(direct_sender_b)
        self.create_notified_message('direct-a-first', chat=direct_a, sender=direct_sender_a)
        self.create_notified_message('direct-b-first', chat=direct_b, sender=direct_sender_b)
        pinned = self.create_group_chat('Pinned group')
        ChatUserPreference.update(pinned, self.recipient, pinned=True)
        for index in range(1, 7):
            self.create_notified_message(f'pinned-{index}', chat=pinned)

        NotificationChannelCursor.process_due()

        body = mail.call_args.kwargs['body']
        self.assertLess(body.index('Pinned group'), body.index('Direct A'))
        self.assertLess(body.index('Direct A'), body.index('Direct B'))
        self.assertIn('pinned\\-1', body)
        self.assertIn('pinned\\-5', body)
        self.assertNotIn('pinned\\-6', body)
        self.assertNotIn('ordinary\\-group\\-omitted', body)
        self.assertIn('And 2 more messages.', body)
        self.assertEqual(NotificationDelivery.objects.filter(status=1).count(), 9)


class NotificationDigestCommandTests(TestCase):
    @patch('User.management.commands.process_notification_digests.NotificationDelivery.process_pending')
    @patch(
        'User.management.commands.process_notification_digests.NotificationChannelCursor.process_due',
        return_value={'dispatches': 3},
    )
    def test_message_and_pending_delivery_share_one_dispatch_budget(self, process_due, process_pending):
        process_pending.return_value = []

        call_command(
            'process_notification_digests',
            limit_users=5,
            limit_deliveries=5,
            stdout=StringIO(),
        )

        process_due.assert_called_once_with(limit_users=5)
        process_pending.assert_called_once_with(limit=2)
