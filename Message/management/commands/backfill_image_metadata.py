from django.core.management.base import BaseCommand

from Message.models import ImageMetadata, Message, MessageTypeChoice


class Command(BaseCommand):
    help = 'Fetch EXIF metadata for existing image messages.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--geocode', action='store_true')
        parser.add_argument('--limit', type=int, default=0)

    def handle(self, *args, **options):
        query = Message.objects.filter(type=MessageTypeChoice.IMAGE, is_deleted=False).order_by('id')
        if not options['force']:
            query = query.filter(image_metadata__isnull=True)
        if options['limit'] > 0:
            query = query[:options['limit']]

        processed = 0
        for message in query.iterator():
            metadata = ImageMetadata.refresh_for_message(message, geocode=options['geocode'])
            processed += 1
            self.stdout.write(f'{message.id}: {"ready" if metadata.status == ImageMetadata.STATUS_READY else metadata.error}')
        self.stdout.write(self.style.SUCCESS(f'Processed {processed} image messages.'))
