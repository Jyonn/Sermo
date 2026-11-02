"""Import trusted repository artwork/audio without resizing or rewriting its bytes."""
import hashlib
import json
import mimetypes
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image
from Sticker.models import ResourceCollection, ManagedResource
from Sticker.resources import upload_object, valid_slug, resource_directory
from PlatformAdmin.models import PlatformAuditLog


class Command(BaseCommand):
    help = 'Import repository activity resources and optionally update live activity/media references.'

    def add_arguments(self, parser):
        parser.add_argument('manifest')
        parser.add_argument('--output', required=True)
        parser.add_argument('--update-references', action='store_true')

    def handle(self, *args, **options):
        path = Path(options['manifest']).resolve()
        items = json.loads(path.read_text())
        prepared = []
        for item in items:
            file = (path.parent / item['file']).resolve()
            if not file.is_relative_to(path.parent) or file.suffix.lower() not in {'.png','.webp','.jpg','.jpeg','.svg','.mp3','.json'}:
                raise CommandError('Invalid source file')
            data = file.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            collection, _ = ResourceCollection.objects.get_or_create(slug=f'activity-{valid_slug(item["activity"])}', defaults=dict(
                title=item['activity'], title_en=item['activity'], category='activity', directory=f'activities/{item["activity"]}', published=True))
            key = f'frienden/assets/{resource_directory(collection)}/{digest}{file.suffix.lower()}'
            mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            prepared.append((item, file, data, digest, collection, key, mime))
        def upload(value):
            _, _, data, _, _, key, mime = value
            upload_object(key, data, mime)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(upload, prepared))
        mapping = {}
        with transaction.atomic():
            for item, file, data, digest, collection, key, mime in prepared:
                size = (0, 0)
                if mime.startswith('image/') and file.suffix != '.svg':
                    with Image.open(file) as image:
                        size = image.size
                legacy_slug=hashlib.sha256(item['alias'].encode()).hexdigest()
                version_slug=hashlib.sha256((item['alias']+'\0'+digest).encode()).hexdigest()
                row=ManagedResource.objects.filter(collection=collection,slug__in=[legacy_slug,version_slug],content_hash=digest).first()
                if row is None:
                    row=ManagedResource.objects.create(collection=collection,slug=version_slug,**dict(
                    title=file.name[:80], title_en=file.name[:80], content_hash=digest, mime_type=mime,
                    storage_key=key, original_key=key, thumbnail_key=key, file_size=len(data), pixel_width=size[0], pixel_height=size[1]))
                mapping[item['alias']] = f'https://api.sermo.jyonn.space/resources/assets/{row.id}/display/{digest}'
            if options['update_references']:
                from Activity.models import ActivityCampaign
                from Message.models import MediaAsset
                def replace(value):
                    if isinstance(value, dict):
                        return {k:replace(v) for k,v in value.items()}
                    if isinstance(value, list):
                        return [replace(v) for v in value]
                    if isinstance(value, str):
                        return mapping.get(value, mapping.get(urlparse(value).path, value))
                    return value
                for campaign in ActivityCampaign.objects.all():
                    updated = replace(campaign.config)
                    if updated != campaign.config:
                        campaign.config = updated
                        campaign.save(update_fields=['config'])
                for asset in MediaAsset.objects.filter(source_key__startswith='activity/'):
                    uri = mapping.get('/' + asset.source_key)
                    if uri:
                        asset.source_uri = uri
                        asset.save(update_fields=['source_uri'])
            PlatformAuditLog.objects.create(action='resource.activity.import', target_type='resources', target_id=0,
                summary=f'{len(mapping)} activity files', metadata={'actor':'management-command'})
        Path(options['output']).write_text(json.dumps(mapping, ensure_ascii=False, indent=2)+'\n')
        self.stdout.write(self.style.SUCCESS(f'Imported {len(mapping)} files; original objects and IDs retained.'))
