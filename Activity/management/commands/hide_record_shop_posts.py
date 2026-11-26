from django.core.management.base import BaseCommand
from django.db.models import F

from Activity.record_shop import MODE
from Square.models import Statement


class Command(BaseCommand):
    help = 'Hide legacy Record Shop congratulations from Square while keeping their medal records.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Hide the posts. By default report the number to hide.')

    def handle(self, *args, **options):
        posts = Statement.objects.filter(
            is_deleted=False,
            recordshopannouncement__published=True,
            recordshopannouncement__run__campaign__config__mode=MODE,
            space_id=F('recordshopannouncement__run__space_id'),
            user_id=F('space__official_user_id'),
        )
        if options['apply']:
            self.stdout.write(f'Hidden posts: {posts.update(is_deleted=True)}')
        else:
            self.stdout.write(f'Posts to hide: {posts.count()}')
