"""Published image assets; never list or delete other objects in the shared bucket."""
import hashlib
import io
import json
import re
import warnings

from django.db import transaction
from django.http import HttpResponseRedirect
from django.views import View
from PIL import Image, ImageOps, UnidentifiedImageError
from qiniu import Auth, BucketManager, put_data
from qiniu.utils import etag_stream

from Config.models import CI
from PlatformAdmin.models import PlatformAuditLog
from Sticker.models import ManagedResource, ResourceCollection, StickerAsset
from Sticker.validators import StickerErrors
from utils import auth
from utils.qiniu import _required_config, avatar_uri_for_key, sign_private_download_url

MAX_BYTES = 10 * 1024 * 1024
CATEGORIES = {'emoji': 384, 'avatar-frame': 512, 'background': 1920, 'profile-card': 1600, 'bubble': 1600, 'illustration': 1600, 'activity': 1920}
SLUG = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')


def valid_slug(value):
    value = str(value or '')
    if not SLUG.fullmatch(value):
        raise StickerErrors.INVALID_HASH
    return value


def collection_json(row, request, admin=False):
    assets = row.resources.all() if admin else row.resources.filter(enabled=True)
    return dict(collection_id=row.id, slug=row.slug, title=row.title, title_en=row.title_en,
                version=row.version, category=row.category, published=row.published,
                directory=resource_directory(row), updated_at=row.updated_at.timestamp(), assets=[item.jsonl(request, admin) for item in assets])


def resource_directory(collection):
    value = collection.directory or f'{dict(emoji="emojis", **{"avatar-frame":"avatar-frames", "background":"backgrounds", "profile-card":"profile-cards", "bubble":"bubbles", "illustration":"illustrations", "activity":"activities"}).get(collection.category, "other")}/{collection.slug}'
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}(?:/[a-z0-9][a-z0-9_-]{0,63}){1,2}', value):
        raise StickerErrors.INVALID_HASH
    return value


def image_variants(data, category):
    if not data or len(data) > MAX_BYTES or category not in CATEGORIES:
        raise StickerErrors.INVALID_IMAGE
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                frames=getattr(source,'n_frames',1)
                if source.format not in {'PNG', 'JPEG', 'WEBP'}:
                    raise StickerErrors.INVALID_IMAGE
                if frames>1:
                    if source.format!='WEBP' or category!='emoji' or frames>60 or source.width*source.height*frames>20_000_000:raise StickerErrors.INVALID_IMAGE
                    images=[];durations=[]
                    for index in range(frames):
                        source.seek(index)
                        frame=source.convert('RGBA');frame.info.clear();images.append(frame)
                        durations.append(max(40,min(2000,int(source.info.get('duration',100)))))
                    if sum(durations)>15000:raise StickerErrors.INVALID_IMAGE
                    original=io.BytesIO();images[0].save(original,'WEBP',save_all=True,append_images=images[1:],duration=durations,loop=0,lossless=True,method=6)
                    display=[]
                    for frame in images:
                        frame.thumbnail((CATEGORIES[category],CATEGORIES[category]),Image.Resampling.LANCZOS);display.append(frame)
                    output=io.BytesIO();display[0].save(output,'WEBP',save_all=True,append_images=display[1:],duration=durations,loop=0,quality=88,method=6)
                    thumb=images[0].copy();thumb.thumbnail((96,96),Image.Resampling.LANCZOS)
                    thumbnail=io.BytesIO();thumb.save(thumbnail,'WEBP',quality=88,method=6)
                    return dict(original=original.getvalue(),display=output.getvalue(),thumbnail=thumbnail.getvalue(),size=display[0].size,original_extension='webp')
                if source.width * source.height > 20_000_000:
                    raise StickerErrors.INVALID_IMAGE
                image = ImageOps.exif_transpose(source).convert('RGBA')
                image.info.clear()
                # Re-encode the original too: remove embedded metadata and active formats.
                original = io.BytesIO()
                image.save(original, 'PNG')
                result = {'original': original.getvalue()}
                for name, size in [('display', CATEGORIES[category]), ('thumbnail', 96)]:
                    copy = image.copy()
                    copy.thumbnail((size, size), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    copy.save(output, 'WEBP', quality=88, method=6)
                    result[name] = output.getvalue()
                result['size'] = image.size
                return result
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise StickerErrors.INVALID_IMAGE


def upload_object(key, data, mime):
    if not re.fullmatch(r'frienden/assets/(?:[a-z0-9][a-z0-9_-]{0,63}/){2,4}[a-f0-9]{64}\.(?:png|webp|jpg|jpeg|svg|mp3|json)', key):
        raise StickerErrors.INVALID_HASH
    bucket = _required_config(CI.QINIU_BUCKET)
    client = Auth(_required_config(CI.QINIU_ACCESS_KEY), _required_config(CI.QINIU_SECRET_KEY))
    token = client.upload_token(bucket, key, 600, {'insertOnly': 1, 'fsizeLimit': len(data)})
    result, info = put_data(token, key, data, mime_type=mime)
    # Content-addressed keys may already exist. Never overwrite shared objects.
    if info.status_code not in (200, 614):
        raise StickerErrors.DOWNLOAD_FAILED
    if info.status_code == 614:
        result, stat = BucketManager(client).stat(bucket, key)
        if stat.status_code != 200 or result.get('fsize') != len(data):
            raise StickerErrors.DOWNLOAD_FAILED
    if not result or result.get('hash') != etag_stream(io.BytesIO(data)):
        raise StickerErrors.DOWNLOAD_FAILED


def publish_resource(collection, slug, title, title_en, data, position=0, upload=upload_object):
    slug = valid_slug(slug)
    variants = image_variants(data, collection.category)
    keys = {}
    for variant in ('original', 'display', 'thumbnail'):
        content = variants[variant]
        digest = hashlib.sha256(content).hexdigest()
        extension = variants.get('original_extension','png') if variant == 'original' else 'webp'
        key = f'frienden/assets/{resource_directory(collection)}/{valid_slug(collection.version)}/{digest}.{extension}'
        upload(key, content, 'image/png' if extension=='png' else 'image/webp')
        keys[variant] = key
    digest = hashlib.sha256(variants['display']).hexdigest()
    with transaction.atomic():
        existing = ManagedResource.objects.filter(collection=collection, slug=slug).first()
        if existing:
            if existing.content_hash != digest:
                raise StickerErrors.INVALID_HASH
            existing.storage_key, existing.thumbnail_key, existing.original_key = keys['display'], keys['thumbnail'], keys['original']
            existing.save(update_fields=['storage_key', 'thumbnail_key', 'original_key'])
            return existing
        sticker = None
        if collection.category == 'emoji':
            with Image.open(io.BytesIO(variants['display'])) as display:
                sticker, _ = StickerAsset.objects.get_or_create(content_hash=digest, defaults=dict(
                    storage_key=keys['display'], mime_type='image/webp', file_size=len(variants['display']),
                    pixel_width=display.width, pixel_height=display.height))
        row = ManagedResource.objects.create(collection=collection, slug=slug, title=title[:80], title_en=title_en[:80],
            content_hash=digest, storage_key=keys['display'], thumbnail_key=keys['thumbnail'], original_key=keys['original'],
            file_size=len(variants['display']), pixel_width=variants['size'][0], pixel_height=variants['size'][1],
            sticker_asset=sticker, position=position)
        collection.save(update_fields=['updated_at'])
        return row


def body(request):
    try:
        value = json.loads(request.body or b'{}')
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise StickerErrors.INVALID_HASH


class ResourceCatalogView(View):
    def get(self, request, slug):
        row = ResourceCollection.objects.filter(slug=slug, published=True).first()
        if not row:
            raise StickerErrors.NOT_FOUND
        return collection_json(row, request)


class ResourceFileView(View):
    def get(self, request, asset_id, variant, digest):
        row = ManagedResource.objects.filter(id=asset_id, content_hash=digest).first()
        if not row or variant not in {'display', 'thumbnail', 'original'}:
            raise StickerErrors.NOT_FOUND
        key = {'display': row.storage_key, 'thumbnail': row.thumbnail_key, 'original': row.original_key}[variant]
        response = HttpResponseRedirect(sign_private_download_url(avatar_uri_for_key(key)))
        response['Cache-Control'] = 'public, max-age=300'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class ResourceAdminView(View):
    @auth.require_platform_admin
    def get(self, request):
        return [collection_json(row, request, True) for row in ResourceCollection.objects.prefetch_related('resources').order_by('slug')]

    @auth.require_platform_admin
    def post(self, request):
        data = body(request)
        slug = valid_slug(data.get('slug'))
        category = data.get('category', 'emoji')
        if category not in {*CATEGORIES, 'activity'} or not str(data.get('title', '')).strip():
            raise StickerErrors.INVALID_IMAGE
        with transaction.atomic():
            existing = ResourceCollection.objects.filter(slug=slug).first()
            if existing and existing.resources.exists() and (existing.category != category or existing.version != data.get('version', 'v1')):
                raise StickerErrors.INVALID_HASH
            row, _ = ResourceCollection.objects.update_or_create(slug=slug, defaults=dict(
                title=str(data['title']).strip()[:80], title_en=str(data.get('title_en', ''))[:80],
                version=valid_slug(data.get('version', 'v1'))[:32], category=category, published=data.get('published') is True))
            PlatformAuditLog.objects.create(action='resource.collection.update', target_type='resource_collection',
                target_id=row.id, summary=row.slug, metadata={'actor': request.platform_admin_email, 'published': row.published})
        return collection_json(row, request, True)


class ResourceAdminAssetView(View):
    @auth.require_platform_admin
    def post(self, request, asset_id):
        data = body(request)
        with transaction.atomic():
            row = ManagedResource.objects.select_for_update().filter(id=asset_id).first()
            if not row:
                raise StickerErrors.NOT_FOUND
            if 'title' in data:
                row.title = str(data['title']).strip()[:80] or row.title
            if 'title_en' in data:
                row.title_en = str(data['title_en'])[:80]
            if 'enabled' in data:
                row.enabled = data['enabled'] is True
            if 'position' in data:
                if type(data['position']) is not int or not 0 <= data['position'] <= 10000:
                    raise StickerErrors.INVALID_HASH
                row.position = data['position']
            row.save()
            row.collection.save(update_fields=['updated_at'])
            PlatformAuditLog.objects.create(action='resource.asset.update', target_type='resource', target_id=row.id,
                summary=row.title, metadata={'actor': request.platform_admin_email, 'enabled': row.enabled})
        return row.jsonl(request, True)


class ResourceAdminUploadView(View):
    @auth.require_platform_admin
    def post(self, request):
        collection = ResourceCollection.objects.filter(slug=request.POST.get('collection')).first()
        file = request.FILES.get('file')
        if not collection or not file or file.size > MAX_BYTES:
            raise StickerErrors.INVALID_IMAGE
        row = publish_resource(collection, request.POST.get('slug'), request.POST.get('title') or file.name,
                               request.POST.get('title_en', ''), file.read(MAX_BYTES + 1))
        PlatformAuditLog.objects.create(action='resource.asset.upload', target_type='resource', target_id=row.id,
            summary=row.title, metadata={'actor': request.platform_admin_email, 'hash': row.content_hash})
        return row.jsonl(request, True)
