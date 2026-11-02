"""Update public labels without touching shared artwork or storage keys."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PlatformAdmin.models import PlatformAuditLog
from Sticker.models import ResourceCollection


class Command(BaseCommand):
    help='Apply reviewed collection labels from a JSON manifest.'

    def add_arguments(self,parser):parser.add_argument('manifest')

    @transaction.atomic
    def handle(self,*args,**options):
        manifest=json.loads(Path(options['manifest']).read_text())
        collection=ResourceCollection.objects.select_for_update().get(slug=manifest['slug'])
        assets={row.slug:row for row in collection.resources.all()}
        labels=manifest['assets']
        if not labels or any(item['slug'] not in assets or not isinstance(item.get('title'),str) or not item['title'].strip() or len(item['title'])>80 for item in labels):raise CommandError('Invalid resource labels')
        for item in labels:
            row=assets[item['slug']];row.title=item['title'].strip();row.title_en=str(item.get('title_en',row.title_en))[:80]
            row.save(update_fields=['title','title_en'])
        collection.save(update_fields=['updated_at'])
        PlatformAuditLog.objects.create(action='resource.labels.update',target_type='resource_collection',target_id=collection.id,summary=collection.slug,metadata={'actor':'management-command','count':len(labels)})
        self.stdout.write(f'{collection.slug}: {len(labels)} labels updated; artwork unchanged')
