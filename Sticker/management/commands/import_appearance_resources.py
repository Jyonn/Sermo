"""Import trusted appearance files without changing animation frames or source bytes."""
import hashlib
import io
import json
import mimetypes
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image
from Sticker.models import ResourceCollection, ManagedResource
from Sticker.resources import CATEGORIES, upload_object, valid_slug, resource_directory
from PlatformAdmin.models import PlatformAuditLog


class Command(BaseCommand):
    help = 'Upload trusted appearance manifest files and emit stable API references.'

    def add_arguments(self, parser):
        parser.add_argument('manifest')
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        path = Path(options['manifest']).resolve()
        prepared = []
        for item in json.loads(path.read_text()):
            file = (path.parent / item['file']).resolve()
            category = item['category']
            if not file.is_relative_to(path.parent) or file.suffix.lower() not in {'.png', '.webp', '.jpg', '.jpeg', '.json'} or category not in CATEGORIES:
                raise CommandError('Invalid appearance manifest')
            data = file.read_bytes()
            if not data or len(data) > 40 * 1024 * 1024:
                raise CommandError('Invalid source size')
            slug = valid_slug(item['collection'])
            row, _ = ResourceCollection.objects.get_or_create(slug=slug, defaults=dict(title=item['title'], title_en=item['title_en'], category=category, published=True))
            if row.category != category:
                raise CommandError('Collection category mismatch')
            digest = hashlib.sha256(data).hexdigest()
            key = f'frienden/assets/{resource_directory(row)}/{digest}{file.suffix.lower()}'
            mime = mimetypes.guess_type(file.name)[0] or 'application/json'
            size = (0, 0)
            thumbnail = data
            if mime.startswith('image/'):
                with Image.open(io.BytesIO(data)) as image:
                    size = image.size
                    preview = image.convert('RGBA')
                    preview.thumbnail((320, 240), Image.Resampling.LANCZOS)
                    output = io.BytesIO(); preview.save(output, 'WEBP', quality=85)
                    thumbnail = output.getvalue()
            elif mime == 'application/json':
                json.loads(data)
            thumb_key = key if thumbnail == data else f'frienden/assets/{resource_directory(row)}/{hashlib.sha256(thumbnail).hexdigest()}.webp'
            prepared.append((item, row, data, key, mime, digest, size, thumbnail, thumb_key))

        def upload(value):
            _, _, data, key, mime, _, _, thumbnail, thumb_key = value
            upload_object(key, data, mime)
            if thumb_key != key:
                upload_object(thumb_key, thumbnail, 'image/webp')
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(upload, prepared))
        mapping = {}
        with transaction.atomic():
            for item, collection, data, key, mime, digest, size, _, thumb_key in prepared:
                slug = hashlib.sha256((item['alias'] + '\0' + digest).encode()).hexdigest()
                row, _ = ManagedResource.objects.get_or_create(collection=collection, slug=slug, defaults=dict(
                    title=item.get('asset_title', Path(item['file']).stem)[:80], title_en=Path(item['file']).stem[:80],
                    content_hash=digest, mime_type=mime, storage_key=key, original_key=key, thumbnail_key=thumb_key,
                    file_size=len(data), pixel_width=size[0], pixel_height=size[1]))
                mapping[item['alias']] = f'https://api.sermo.jyonn.space/resources/assets/{row.id}/display/{digest}'
            PlatformAuditLog.objects.create(action='resource.appearance.import', target_type='resources', target_id=0,
                summary=f'{len(mapping)} appearance files', metadata={'actor': 'management-command'})
        Path(options['output']).write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + '\n')
        self.stdout.write(self.style.SUCCESS(f'Imported {len(mapping)} appearance files.'))
