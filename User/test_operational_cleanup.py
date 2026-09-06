import datetime
import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from PlatformAdmin.models import PlatformAdminEmailCode
from Space.models import Space, SpaceEmailCodePurposeChoice, SpaceEmailVerificationCode, SpacePhoneVerificationCode
from User.models import (
    AccountSwitchTicket,
    InstantNotificationVerification,
    NotificationDelivery,
    NotificationDeliveryStatusChoice,
    NotificationEvent,
    NotificationEventTypeChoice,
    OfficialLoginTicket,
    RefreshToken,
    User,
    UserContactVerificationCode,
    UserNotificationChoice,
    UserPasswordRecoveryChallenge,
    WebPushSubscription,
)


class OperationalCleanupCommandTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.old = self.now - datetime.timedelta(days=100)
        self.space = Space.objects.create(name='Cleanup', slug='cleanup', email='cleanup@example.com')
        self.user = User.create(self.space, 'Cleanup User', verified=True)
        self.other = User.create(self.space, 'Other User', verified=True)

    @staticmethod
    def _set_created_at(instance, value):
        type(instance).objects.filter(pk=instance.pk).update(created_at=value)

    def _create_delivery(self, status, created_at):
        event = NotificationEvent.objects.create(
            space=self.space,
            user=self.user,
            event_type=NotificationEventTypeChoice.SYSTEM,
            payload={'kind': 'cleanup_test'},
        )
        delivery = NotificationDelivery.objects.create(
            event=event,
            channel=UserNotificationChoice.EMAIL,
            status=status,
        )
        self._set_created_at(delivery, created_at)
        return event, delivery

    def _create_expired_credentials(self):
        expired_at = self.now - datetime.timedelta(days=8)
        RefreshToken.objects.create(user=self.user, jti='expired', expires_at=self.now - datetime.timedelta(seconds=1))
        RefreshToken.objects.create(
            user=self.user,
            jti='revoked',
            expires_at=self.now + datetime.timedelta(days=10),
            revoked_at=expired_at,
        )
        OfficialLoginTicket.objects.create(
            space=self.space, user=self.user, token='official', expires_at=expired_at,
        )
        AccountSwitchTicket.objects.create(
            source_user=self.user, target_user=self.other, token='switch', expires_at=expired_at,
        )
        UserContactVerificationCode.objects.create(
            user=self.user, channel=UserNotificationChoice.EMAIL, target='user@example.com', code='123456',
            expires_at=expired_at,
        )
        InstantNotificationVerification.objects.create(
            user=self.user, provider='bark', target='https://api.day.app/test', code='123456',
            expires_at=expired_at,
        )
        UserPasswordRecoveryChallenge.objects.create(
            user=self.user, channel=UserNotificationChoice.EMAIL, target='user@example.com', code='123456',
            code_expires_at=expired_at,
        )
        SpacePhoneVerificationCode.objects.create(
            space=self.space, phone='+8613900000000', code='123456', expires_at=expired_at,
        )
        SpaceEmailVerificationCode.objects.create(
            space=self.space, email='admin@example.com', purpose=SpaceEmailCodePurposeChoice.LOGIN,
            code='123456', expires_at=expired_at,
        )
        PlatformAdminEmailCode.objects.create(
            email='platform@example.com', code='123456', expires_at=expired_at,
        )

    def test_dry_run_reports_candidates_without_deleting(self):
        _event, delivery = self._create_delivery(NotificationDeliveryStatusChoice.SENT, self.old)
        self._create_expired_credentials()
        output = StringIO()

        call_command('prune_operational_data', stdout=output)

        summary = json.loads(output.getvalue())
        self.assertEqual(summary['mode'], 'dry-run')
        self.assertEqual(summary['skipped_tables'], [])
        self.assertEqual(summary['tables']['notification_deliveries'], 1)
        self.assertEqual(summary['tables']['refresh_tokens'], 2)
        self.assertTrue(NotificationDelivery.objects.filter(pk=delivery.pk).exists())
        self.assertEqual(RefreshToken.objects.count(), 2)

    def test_execute_prunes_only_expired_operational_records(self):
        old_event, old_delivery = self._create_delivery(NotificationDeliveryStatusChoice.SENT, self.old)
        _pending_event, pending_delivery = self._create_delivery(NotificationDeliveryStatusChoice.PENDING, self.old)
        _recent_event, recent_delivery = self._create_delivery(NotificationDeliveryStatusChoice.SENT, self.now)
        self._create_expired_credentials()
        active_token = RefreshToken.objects.create(
            user=self.user, jti='active', expires_at=self.now + datetime.timedelta(days=10),
        )
        recent_code = UserContactVerificationCode.objects.create(
            user=self.user,
            channel=UserNotificationChoice.EMAIL,
            target='recent@example.com',
            code='654321',
            expires_at=self.now - datetime.timedelta(days=1),
        )
        subscription = WebPushSubscription.objects.create(
            user=self.user,
            space=self.space,
            endpoint='https://push.example.com/old',
            endpoint_digest='old-endpoint',
            p256dh='key',
            auth='auth',
            origin=WebPushSubscription.CANONICAL_WEB_ORIGIN,
        )
        WebPushSubscription.objects.filter(pk=subscription.pk).update(last_seen_at=self.old)
        recent_subscription = WebPushSubscription.objects.create(
            user=self.user,
            space=self.space,
            endpoint='https://push.example.com/recent',
            endpoint_digest='recent-endpoint',
            p256dh='key',
            auth='auth',
            origin=WebPushSubscription.CANONICAL_WEB_ORIGIN,
            enabled=False,
        )

        call_command('prune_operational_data', '--execute', '--batch-size=2', stdout=StringIO())

        self.assertFalse(NotificationDelivery.objects.filter(pk=old_delivery.pk).exists())
        self.assertTrue(NotificationDelivery.objects.filter(pk=pending_delivery.pk).exists())
        self.assertTrue(NotificationDelivery.objects.filter(pk=recent_delivery.pk).exists())
        self.assertTrue(NotificationEvent.objects.filter(pk=old_event.pk).exists())
        self.assertEqual(list(RefreshToken.objects.values_list('jti', flat=True)), [active_token.jti])
        self.assertFalse(OfficialLoginTicket.objects.exists())
        self.assertFalse(AccountSwitchTicket.objects.exists())
        self.assertEqual(
            list(UserContactVerificationCode.objects.values_list('id', flat=True)),
            [recent_code.id],
        )
        self.assertFalse(InstantNotificationVerification.objects.exists())
        self.assertFalse(UserPasswordRecoveryChallenge.objects.exists())
        self.assertFalse(SpacePhoneVerificationCode.objects.exists())
        self.assertFalse(SpaceEmailVerificationCode.objects.exists())
        self.assertFalse(PlatformAdminEmailCode.objects.exists())
        self.assertEqual(
            list(WebPushSubscription.objects.values_list('id', flat=True)),
            [recent_subscription.id],
        )

        output = StringIO()
        call_command('prune_operational_data', '--execute', '--batch-size=2', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['total'], 0)
