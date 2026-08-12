from django.core.management.base import BaseCommand
from django.db.models import Q

from Message.models import MediaAsset


class Command(BaseCommand):
    help = 'Backfill missing Qiniu metadata and reverse-geocoded addresses for all media assets.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Refresh Qiniu metadata even when it is complete.')
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--skip-geocode', action='store_true')

    def handle(self, *args, **options):
        media_kinds = (MediaAsset.KIND_IMAGE, MediaAsset.KIND_VIDEO)
        query = MediaAsset.objects.filter(kind__in=media_kinds).order_by('id')
        if not options['force']:
            query = query.filter(
                Q(status__in=(MediaAsset.STATUS_PENDING, MediaAsset.STATUS_FAILED))
                | Q(file_size__isnull=True)
                | Q(pixel_width__isnull=True)
                | Q(pixel_height__isnull=True)
                | Q(detail_metadata_checked_at__isnull=True)
                | ~Q(detail_metadata_error='')
                | (Q(latitude__isnull=False) & Q(longitude__isnull=False) & Q(address=''))
            )
        if options['limit'] > 0:
            query = query[:options['limit']]

        refreshed = 0
        geocoded = 0
        failed = 0
        for asset in query.iterator():
            needs_metadata = options['force'] or any((
                asset.status != MediaAsset.STATUS_READY,
                asset.file_size is None,
                asset.pixel_width is None,
                asset.pixel_height is None,
                asset.detail_metadata_checked_at is None,
                bool(asset.detail_metadata_error),
            ))
            if needs_metadata:
                MediaAsset.refresh(asset, geocode=not options['skip_geocode'])
                refreshed += 1
                if asset.status == MediaAsset.STATUS_FAILED:
                    failed += 1
            elif not options['skip_geocode'] and asset.latitude is not None and asset.longitude is not None and not asset.address:
                MediaAsset.refresh_geocoding(asset)
                geocoded += 1
            result = asset.address or asset.error or asset.geocoding_error or 'ready'
            self.stdout.write(f'{asset.id} {asset.source_key}: {result}')

        self.stdout.write(self.style.SUCCESS(
            f'Processed {refreshed + geocoded} media assets '
            f'(metadata={refreshed}, geocoded={geocoded}, failed={failed}).'
        ))
