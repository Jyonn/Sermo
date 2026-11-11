import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from Chat.voice import reconcile


class Command(BaseCommand):
    help = 'Expire foreground calls and retry media-room cleanup. Run as a supervised worker.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                reconcile()
            except Exception as exc:
                self.stderr.write(f'Voice watchdog failed: {type(exc).__name__}')
                if options['once']:
                    raise
            finally:
                close_old_connections()
            if options['once']:
                return
            time.sleep(1)
