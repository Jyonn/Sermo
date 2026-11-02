"""Repeatable CLI for agents/operators; uses the server Config, never prints credentials."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from PlatformAdmin.models import PlatformAuditLog
from Sticker.models import ResourceCollection
from Sticker.resources import publish_resource, valid_slug, CATEGORIES


class Command(BaseCommand):
    help = 'Upload an image collection from a local JSON manifest; old resources remain intact.'

    def add_arguments(self, parser):
        parser.add_argument('manifest')
        parser.add_argument('--publish', action='store_true')

    def handle(self, *args, **options):
        path = Path(options['manifest']).resolve()
        manifest = json.loads(path.read_text())
        slug = valid_slug(manifest['slug'])
        version = valid_slug(manifest.get('version', 'v1'))
        category = manifest.get('category', 'emoji')
        if category not in CATEGORIES:
            raise CommandError('Invalid category')
        collection, _ = ResourceCollection.objects.get_or_create(slug=slug, defaults=dict(
            title=manifest['title'], title_en=manifest.get('title_en', ''), version=version, category=category))
        if collection.version != version or collection.category != category:
            raise CommandError('Use a new collection slug for a new version/category')
        for index, item in enumerate(manifest['assets']):
            file = (path.parent / item['file']).resolve()
            if not file.is_relative_to(path.parent):
                raise CommandError('File outside manifest directory')
            row = publish_resource(collection, item['slug'], item['title'], item.get('title_en', ''), file.read_bytes(), index)
            self.stdout.write(f'{row.slug}: resource #{row.id}, sha256 {row.content_hash}')
        if options['publish']:
            collection.published = True
            collection.save(update_fields=['published', 'updated_at'])
        PlatformAuditLog.objects.create(action='resource.collection.import', target_type='resource_collection',
            target_id=collection.id, summary=slug, metadata={'actor': 'management-command', 'count': len(manifest['assets']), 'published': collection.published})
        self.stdout.write(self.style.SUCCESS(f'{slug}: {collection.resources.count()} resources, published={collection.published}'))
