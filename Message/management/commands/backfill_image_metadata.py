from django.core.management.base import BaseCommand
from Message.models import MediaAsset, Message, MessageTypeChoice


class Command(BaseCommand):
    help = 'Fetch imageInfo, EXIF, and optional location metadata for existing image messages.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--geocode', action='store_true')
        parser.add_argument(
            '--geocode-missing',
            action='store_true',
            help='Only resolve image metadata that has coordinates but no address.',
        )
        parser.add_argument('--limit', type=int, default=0)

    def handle(self, *args, **options):
        if options['geocode_missing']:
            self._geocode_missing(options['limit'])
            return

        query = Message.objects.filter(type=MessageTypeChoice.IMAGE, is_deleted=False).order_by('id')
        if options['limit'] > 0:
            query = query[:options['limit']]

        processed = 0
        for message in query.iterator():
            source_key = message.source_media_key()
            metadata = MediaAsset.objects.filter(source_key=source_key).first()
            if not options['force'] and metadata and all((metadata.file_size, metadata.pixel_width, metadata.pixel_height)):
                continue
            if metadata is None:
                metadata = MediaAsset.objects.create(
                    source_key=source_key,
                    source_uri=message.source_media_uri(),
                    kind=MediaAsset.KIND_IMAGE,
                )
            metadata = MediaAsset.refresh(metadata, geocode=options['geocode'])
            processed += 1
            self.stdout.write(f'{message.id}: {"ready" if metadata.status == MediaAsset.STATUS_READY else metadata.error}')
        self.stdout.write(self.style.SUCCESS(f'Processed {processed} image messages.'))

    def _geocode_missing(self, limit):
        query = MediaAsset.objects.filter(
            kind=MediaAsset.KIND_IMAGE,
            latitude__isnull=False,
            longitude__isnull=False,
            address='',
        ).order_by('id')
        if limit > 0:
            query = query[:limit]

        processed = 0
        for metadata in query.iterator():
            MediaAsset.refresh_geocoding(metadata)
            processed += 1
            result = metadata.address or metadata.geocoding_error or 'no address returned'
            self.stdout.write(f'{metadata.source_key}: {result}')
        self.stdout.write(self.style.SUCCESS(f'Geocoded {processed} image messages.'))
