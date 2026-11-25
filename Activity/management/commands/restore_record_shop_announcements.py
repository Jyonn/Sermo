from django.core.management.base import BaseCommand
from django.db import transaction

from Activity.models import RecordShopAnnouncement
from Activity.record_shop import statement_announcement_payload
from Square.models import Statement, StatementVisibilityChoice


class Command(BaseCommand):
    help = 'Restore linked Record Shop first-discovery posts at their original unlock time.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write changes. By default only report the planned changes.')

    @transaction.atomic
    def handle(self, *args, **options):
        announcements = RecordShopAnnouncement.objects.select_for_update().filter(published=True).order_by('pk')
        restored = corrected = skipped = 0
        for announcement in announcements:
            statement = Statement.objects.select_for_update().select_related('user').filter(pk=announcement.statement_id).first()
            if (statement is None or statement.space_id != announcement.run.space_id
                    or not statement.user.is_official or statement.is_anonymous
                    or statement.visibility != StatementVisibilityChoice.PUBLIC
                    or statement_announcement_payload(statement) is None):
                skipped += 1
                continue
            if not statement.is_deleted and statement.created_at == announcement.discovered_at:
                continue
            if statement.is_deleted:
                restored += 1
            if statement.created_at != announcement.discovered_at:
                corrected += 1
            if options['apply']:
                statement.is_deleted = False
                statement.created_at = announcement.discovered_at
                statement.save(update_fields=['is_deleted', 'created_at'])
        mode = 'Applied' if options['apply'] else 'Preview'
        self.stdout.write(f'{mode}: restored={restored}, corrected_time={corrected}, skipped={skipped}')
