import json

from django.core.management.base import BaseCommand, CommandError

from User.native_push_runtime import process_native_queue


class Command(BaseCommand):
    help = 'Inspect a bounded native notification batch; --send also requires explicit server gates.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--send', action='store_true')

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 500:
            raise CommandError('--limit must be between 1 and 500.')
        summary = process_native_queue(limit=options['limit'], send=options['send'])
        self.stdout.write(json.dumps(summary, sort_keys=True))
