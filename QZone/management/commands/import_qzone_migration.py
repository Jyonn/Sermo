from django.core.management.base import BaseCommand

from QZone.importer import QZoneImporter, resolve_space


class Command(BaseCommand):
    help = 'Import a qzone-migration.json file into source tables and Square projections.'

    def add_arguments(self, parser):
        parser.add_argument('--space', required=True, help='Target space slug.')
        parser.add_argument('--input', required=True, help='Path to qzone-migration.json.')
        parser.add_argument(
            '--stage',
            choices=('preflight', 'source', 'identity', 'media', 'projection', 'verify', 'all'),
            default='preflight',
        )
        parser.add_argument('--batch-size', type=int, default=500)
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--upload-media', action='store_true')
        parser.add_argument('--retry-failed', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        space = resolve_space(options['space'])
        importer = QZoneImporter(
            space,
            options['input'],
            stdout=self.stdout,
            batch_size=options['batch_size'],
        )
        importer.preflight()
        if options['dry_run'] or options['stage'] == 'preflight':
            return

        space.require_qq_binding_granted()
        stage = options['stage']
        limit = max(0, options['limit'])
        if stage in ('source', 'all'):
            importer.import_source(limit=limit)
        if stage in ('identity', 'all'):
            importer.import_identities(limit=limit)
        if stage in ('media', 'all'):
            importer.prepare_media(limit=limit)
            if options['upload_media']:
                importer.upload_media(limit=limit, retry_failed=options['retry_failed'])
        if stage in ('projection', 'all'):
            importer.project(limit=limit)
        if stage in ('verify', 'all'):
            importer.verify()
