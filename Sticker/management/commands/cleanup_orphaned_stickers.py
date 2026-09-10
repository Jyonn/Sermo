from django.core.management.base import BaseCommand

from Message.models import Message, MessageTypeChoice
from Sticker.models import StickerAsset, UserSticker
from Sticker.services import delete_unreferenced_sticker_asset


class Command(BaseCommand):
    help = 'Delete sticker assets referenced only by deleted messages.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=500)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        batch_size = max(1, options['batch_size'])
        last_id = 0
        scanned = 0
        deleted = 0

        while True:
            assets = list(
                StickerAsset.objects.filter(id__gt=last_id)
                .order_by('id')[:batch_size]
            )
            if not assets:
                break
            last_id = assets[-1].id
            scanned += len(assets)
            asset_ids = [asset.id for asset in assets]
            owned_ids = set(UserSticker.objects.filter(
                asset_id__in=asset_ids,
            ).values_list('asset_id', flat=True))
            content_to_id = {
                f'{{"kind":"sticker","asset_id":{asset_id}}}': asset_id
                for asset_id in asset_ids
            }
            active_contents = Message.objects.filter(
                type=MessageTypeChoice.STICKER,
                is_deleted=False,
                content__in=content_to_id,
            ).values_list('content', flat=True).distinct()
            active_ids = {content_to_id[content] for content in active_contents}

            candidates = [
                asset for asset in assets
                if asset.id not in owned_ids and asset.id not in active_ids
            ]
            if options['dry_run']:
                deleted += len(candidates)
            else:
                for asset in candidates:
                    deleted += int(delete_unreferenced_sticker_asset(asset))

            self.stdout.write(
                f'Processed {scanned} sticker assets; '
                f'{"would delete" if options["dry_run"] else "deleted"} {deleted}.',
                ending='\r',
            )

        self.stdout.write('')
        summary = f'Scanned {scanned} sticker assets; '
        if options['dry_run']:
            summary += f'would delete {deleted} orphaned assets.'
            self.stdout.write(self.style.WARNING(f'Dry run: {summary}'))
        else:
            summary += f'deleted {deleted} orphaned assets.'
            self.stdout.write(self.style.SUCCESS(summary))
