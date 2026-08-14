import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from User.models import NotificationChannelCursor


class Command(BaseCommand):
    help = 'Send due aggregated chat notifications for offline users.'
    LOCK_NAME = 'sermo_notification_digest_worker'

    def add_arguments(self, parser):
        parser.add_argument('--limit-users', type=int, default=200)

    def handle(self, *args, **options):
        limit_users = options['limit_users']
        if limit_users < 1:
            raise CommandError('--limit-users must be a positive integer.')

        with connection.cursor() as cursor:
            cursor.execute('SELECT GET_LOCK(%s, 0)', [self.LOCK_NAME])
            acquired = cursor.fetchone()[0] == 1
        if not acquired:
            self.stdout.write('Skipped: another notification digest task is still running.')
            return

        try:
            summary = NotificationChannelCursor.process_due(limit_users=limit_users)
            self.stdout.write(self.style.SUCCESS(json.dumps(summary, ensure_ascii=False, sort_keys=True)))
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT RELEASE_LOCK(%s)', [self.LOCK_NAME])
