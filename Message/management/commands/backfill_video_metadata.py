from django.core.management.base import BaseCommand
from Message.models import MediaMetadata, Message, MessageTypeChoice


class Command(BaseCommand):
    help = 'Fetch avinfo and optional location metadata for existing video messages.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--geocode', action='store_true')
        parser.add_argument('--limit', type=int, default=0)

    def handle(self, *args, **options):
        query = Message.objects.filter(type=MessageTypeChoice.VIDEO, is_deleted=False).order_by('id')
        if options['limit'] > 0:
            query = query[:options['limit']]

        processed = 0
        for message in query.iterator():
            source_key = message.source_media_key()
            metadata = MediaMetadata.objects.filter(source_key=source_key).first()
            if not options['force'] and metadata and all((metadata.file_size, metadata.pixel_width, metadata.pixel_height)):
                continue
            if metadata is None:
                metadata = MediaMetadata.objects.create(
                    source_key=source_key,
                    source_uri=message.source_media_uri(),
                    kind=MediaMetadata.KIND_VIDEO,
                )
            metadata = MediaMetadata.refresh(metadata, geocode=options['geocode'])
            processed += 1
            result = 'ready' if metadata.status == MediaMetadata.STATUS_READY else metadata.error
            self.stdout.write(f'{message.id}: {result}')
        self.stdout.write(self.style.SUCCESS(f'Processed {processed} video messages.'))
