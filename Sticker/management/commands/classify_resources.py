"""Reorganize registered resources without changing any published URL or object."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from Sticker.models import ManagedResource, ResourceCollection
from Sticker.resources import CATEGORIES, valid_slug
from PlatformAdmin.models import PlatformAuditLog


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('manifest')

    @transaction.atomic
    def handle(self, *args, **options):
        items = json.loads(Path(options['manifest']).read_text())
        for item in items:
            if item['category'] not in CATEGORIES:
                raise CommandError('Invalid resource category')
            collection, _ = ResourceCollection.objects.get_or_create(slug=valid_slug(item['collection']), defaults=dict(
                title=item['title'], title_en=item['title_en'], category=item['category'], published=True))
            if collection.category != item['category']:
                raise CommandError('Collection category mismatch')
            row = ManagedResource.objects.select_for_update().get(id=item['resource_id'])
            row.collection = collection
            row.title = item['asset_title'][:80]
            row.title_en = item.get('asset_title_en', item['asset_title'])[:80]
            row.save(update_fields=['collection', 'title', 'title_en'])
        PlatformAuditLog.objects.create(action='resource.classify', target_type='resources', target_id=0,
            summary=f'{len(items)} resources', metadata={'actor':'management-command'})
        self.stdout.write(f'Classified {len(items)} resources; IDs and storage keys preserved.')
