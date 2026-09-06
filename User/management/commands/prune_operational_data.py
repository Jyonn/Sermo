import datetime
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from PlatformAdmin.models import PlatformAdminEmailCode
from Space.models import SpaceEmailVerificationCode, SpacePhoneVerificationCode
from User.models import (
    AccountSwitchTicket,
    InstantNotificationVerification,
    NotificationDelivery,
    NotificationDeliveryStatusChoice,
    OfficialLoginTicket,
    RefreshToken,
    UserContactVerificationCode,
    UserPasswordRecoveryChallenge,
    WebPushSubscription,
)


class Command(BaseCommand):
    help = 'Prune expired operational records without deleting user content or synchronization events.'

    def add_arguments(self, parser):
        parser.add_argument('--execute', action='store_true', help='Delete matching rows. Omit for a dry run.')
        parser.add_argument('--batch-size', type=int, default=1000)
        parser.add_argument('--delivery-days', type=int, default=30)
        parser.add_argument('--credential-grace-days', type=int, default=7)
        parser.add_argument('--web-push-stale-days', type=int, default=90)

    @staticmethod
    def _delete_in_batches(queryset, batch_size):
        deleted = 0
        while True:
            ids = list(queryset.order_by('pk').values_list('pk', flat=True)[:batch_size])
            if not ids:
                return deleted
            with transaction.atomic():
                queryset.model._base_manager.filter(pk__in=ids).delete()
            deleted += len(ids)

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        delivery_days = options['delivery_days']
        credential_grace_days = options['credential_grace_days']
        web_push_stale_days = options['web_push_stale_days']
        for name, value in (
            ('--batch-size', batch_size),
            ('--delivery-days', delivery_days),
            ('--credential-grace-days', credential_grace_days),
            ('--web-push-stale-days', web_push_stale_days),
        ):
            if value < 1:
                raise CommandError(f'{name} must be a positive integer.')

        now = timezone.now()
        delivery_cutoff = now - datetime.timedelta(days=delivery_days)
        credential_cutoff = now - datetime.timedelta(days=credential_grace_days)
        web_push_cutoff = now - datetime.timedelta(days=web_push_stale_days)
        rules = {
            'notification_deliveries': NotificationDelivery.objects.filter(
                status__in=(
                    NotificationDeliveryStatusChoice.SENT,
                    NotificationDeliveryStatusChoice.FAILED,
                    NotificationDeliveryStatusChoice.SKIPPED,
                ),
                created_at__lt=delivery_cutoff,
            ),
            'refresh_tokens': RefreshToken.objects.filter(
                Q(expires_at__lt=now) | Q(revoked_at__lt=credential_cutoff),
            ),
            'official_login_tickets': OfficialLoginTicket.objects.filter(expires_at__lt=credential_cutoff),
            'account_switch_tickets': AccountSwitchTicket.objects.filter(expires_at__lt=credential_cutoff),
            'contact_verification_codes': UserContactVerificationCode.objects.filter(
                expires_at__lt=credential_cutoff,
            ),
            'instant_notification_verifications': InstantNotificationVerification.objects.filter(
                expires_at__lt=credential_cutoff,
            ),
            'password_recovery_challenges': UserPasswordRecoveryChallenge.objects.filter(
                code_expires_at__lt=credential_cutoff,
            ),
            'space_phone_verification_codes': SpacePhoneVerificationCode.objects.filter(
                expires_at__lt=credential_cutoff,
            ),
            'space_email_verification_codes': SpaceEmailVerificationCode.objects.filter(
                expires_at__lt=credential_cutoff,
            ),
            'platform_admin_email_codes': PlatformAdminEmailCode.objects.filter(
                expires_at__lt=credential_cutoff,
            ),
            'web_push_subscriptions': WebPushSubscription.objects.filter(
                Q(last_seen_at__lt=web_push_cutoff)
                | Q(enabled=False, last_seen_at__lt=credential_cutoff),
            ),
        }

        existing_tables = {name.lower() for name in connection.introspection.table_names()}
        skipped_tables = [
            name for name, queryset in rules.items()
            if queryset.model._meta.db_table.lower() not in existing_tables
        ]
        available_rules = {
            name: queryset for name, queryset in rules.items()
            if queryset.model._meta.db_table.lower() in existing_tables
        }
        counts = {name: queryset.count() for name, queryset in available_rules.items()}
        if options['execute']:
            counts = {
                name: self._delete_in_batches(queryset, batch_size)
                for name, queryset in available_rules.items()
            }
        summary = {
            'mode': 'execute' if options['execute'] else 'dry-run',
            'skipped_tables': skipped_tables,
            'total': sum(counts.values()),
            'tables': counts,
        }
        self.stdout.write(self.style.SUCCESS(json.dumps(summary, ensure_ascii=False, sort_keys=True)))
