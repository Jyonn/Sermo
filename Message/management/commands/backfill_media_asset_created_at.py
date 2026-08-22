from django.core.management.base import BaseCommand
from django.db.models import Min

from Message.models import MediaAsset


class Command(BaseCommand):
    help = 'Backfill media asset creation times from their earliest message or forwarded snapshot.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        queryset = MediaAsset.objects.order_by('id')
        if options['limit'] > 0:
            queryset = queryset[:options['limit']]

        updated = unchanged = missing = 0
        for asset in queryset.iterator(chunk_size=500):
            references = asset.resources.aggregate(
                message_time=Min('messages__created_at'),
                forwarded_time=Min('forward_items__sent_at'),
            )
            candidates = [value for value in references.values() if value is not None]
            if not candidates:
                missing += 1
                continue
            first_uploaded_at = min(candidates)
            if asset.created_at == first_uploaded_at:
                unchanged += 1
                continue
            if not options['dry_run']:
                MediaAsset.objects.filter(id=asset.id).update(created_at=first_uploaded_at)
            updated += 1
            self.stdout.write(f'{asset.id}: {asset.created_at.isoformat()} -> {first_uploaded_at.isoformat()}')

        self.stdout.write(self.style.SUCCESS(
            f'updated={updated} unchanged={unchanged} missing={missing}',
        ))
