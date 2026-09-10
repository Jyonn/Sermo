import json

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Max

from Message.models import Message, MessageTypeChoice
from Sticker.models import StickerAsset, UserStickerUsage


class Command(BaseCommand):
    help = 'Rebuild per-user sticker usage from existing sticker messages.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=1000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        batch_size = max(1, options['batch_size'])
        aggregates = Message.objects.filter(
            type=MessageTypeChoice.STICKER,
            is_deleted=False,
        ).values('user_id', 'content').annotate(
            use_count=Count('id'),
            last_used_at=Max('created_at'),
        ).iterator(chunk_size=batch_size)

        rows = []
        rebuilt_count = 0
        skipped = 0

        def flush_rows():
            nonlocal rebuilt_count, skipped
            if not rows:
                return
            valid_asset_ids = set(StickerAsset.objects.filter(
                id__in={row.asset_id for row in rows},
            ).values_list('id', flat=True))
            valid_rows = [row for row in rows if row.asset_id in valid_asset_ids]
            skipped += len(rows) - len(valid_rows)
            if not options['dry_run']:
                UserStickerUsage.objects.bulk_create(valid_rows, batch_size=batch_size)
            rebuilt_count += len(valid_rows)
            rows.clear()

        def run_rebuild():
            nonlocal skipped
            for aggregate in aggregates:
                try:
                    asset_id = int(json.loads(aggregate['content']).get('asset_id'))
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    skipped += 1
                    continue
                rows.append(UserStickerUsage(
                    user_id=aggregate['user_id'],
                    asset_id=asset_id,
                    use_count=aggregate['use_count'],
                    last_used_at=aggregate['last_used_at'],
                ))
                if len(rows) >= batch_size:
                    flush_rows()
            flush_rows()

        if options['dry_run']:
            run_rebuild()
            self.stdout.write(self.style.WARNING(
                f'Dry run: would rebuild {rebuilt_count} sticker usage rows; '
                f'skipped {skipped} invalid groups.'
            ))
            return

        # Rebuild atomically so readers never observe a partially replaced usage table.
        # The iterator and write buffers stay bounded by --batch-size.
        with transaction.atomic():
            UserStickerUsage.objects.all().delete()
            run_rebuild()
        self.stdout.write(self.style.SUCCESS(
            f'Rebuilt {rebuilt_count} sticker usage rows; skipped {skipped} invalid groups.'
        ))
