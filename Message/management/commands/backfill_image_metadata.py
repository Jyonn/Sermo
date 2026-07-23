from django.core.management.base import BaseCommand
from django.db.models import Q

from Message.models import ImageMetadata, Message, MessageTypeChoice


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
        if not options['force']:
            query = query.filter(
                Q(image_metadata__isnull=True)
                | Q(image_metadata__file_size__isnull=True)
                | Q(image_metadata__pixel_width__isnull=True)
                | Q(image_metadata__pixel_height__isnull=True)
            )
        if options['limit'] > 0:
            query = query[:options['limit']]

        processed = 0
        for message in query.iterator():
            metadata = ImageMetadata.refresh_for_message(message, geocode=options['geocode'])
            processed += 1
            self.stdout.write(f'{message.id}: {"ready" if metadata.status == ImageMetadata.STATUS_READY else metadata.error}')
        self.stdout.write(self.style.SUCCESS(f'Processed {processed} image messages.'))

    def _geocode_missing(self, limit):
        query = ImageMetadata.objects.filter(
            message__type=MessageTypeChoice.IMAGE,
            message__is_deleted=False,
            latitude__isnull=False,
            longitude__isnull=False,
            address='',
        ).order_by('message_id')
        if limit > 0:
            query = query[:limit]

        processed = 0
        for metadata in query.iterator():
            ImageMetadata.refresh_geocoding(metadata)
            processed += 1
            result = metadata.address or metadata.geocoding_error or 'no address returned'
            self.stdout.write(f'{metadata.message_id}: {result}')
        self.stdout.write(self.style.SUCCESS(f'Geocoded {processed} image messages.'))
