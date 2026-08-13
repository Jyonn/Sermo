from django.core.management.base import BaseCommand
from django.db.models import Q

from Sticker.models import StickerAsset


class Command(BaseCommand):
    help = 'Fetch missing sticker width and height from Qiniu imageInfo.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Refresh every sticker asset.')
        parser.add_argument('--limit', type=int, default=0, help='Maximum number of assets to process; 0 means all.')

    def handle(self, *args, **options):
        queryset = StickerAsset.objects.order_by('id')
        if not options['force']:
            queryset = queryset.filter(
                Q(pixel_width__isnull=True)
                | Q(pixel_height__isnull=True)
                | Q(dimensions_checked_at__isnull=True)
                | ~Q(dimensions_error='')
            )
        if options['limit'] > 0:
            queryset = queryset[:options['limit']]

        refreshed = 0
        failed = 0
        for asset in queryset.iterator() if not options['limit'] else queryset:
            StickerAsset.refresh_dimensions(asset)
            if asset.pixel_width and asset.pixel_height:
                refreshed += 1
                self.stdout.write(f'{asset.id}: {asset.pixel_width}x{asset.pixel_height}')
            else:
                failed += 1
                self.stderr.write(f'{asset.id}: {asset.dimensions_error or "dimensions unavailable"}')
        self.stdout.write(self.style.SUCCESS(f'Processed {refreshed + failed} sticker assets (ready={refreshed}, failed={failed}).'))
