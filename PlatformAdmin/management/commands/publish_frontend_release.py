"""Deployment uses the existing SSH authorization, without persisting admin tokens."""
import json
import sys
from django.core.management.base import BaseCommand, CommandError
from PlatformAdmin.frontend_releases import publish_frontend_release


class Command(BaseCommand):
    help='Register frontend release notes from stdin after verifying the deployed site.'

    def add_arguments(self,parser):parser.add_argument('--origin',required=True)

    def handle(self,*args,**options):
        try:
            raw=sys.stdin.read(32769)
            if len(raw.encode())>32768:raise ValueError('Release notes too large')
            row=publish_frontend_release(options['origin'],json.loads(raw),'deployment-command')
        except (ValueError,TypeError,KeyError) as error:raise CommandError(str(error))
        self.stdout.write(f'Frontend release registered: {row.version} at {options["origin"]}')
