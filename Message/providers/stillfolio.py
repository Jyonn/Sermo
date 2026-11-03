"""Resolve public Stillfolio collections through their documented share API."""

import re
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import requests


class StillfolioProvider:
    HOST = 'stillfolio.jyonn.space'
    API_HOST = 'api.stillfolio.jyonn.space'
    SHARE_RE = re.compile(r'^/share/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$', re.I)

    @staticmethod
    def photo_metadata(photo):
        """Expose only public photography fields, not client IDs or local file names."""
        raw = photo.get('metadata')
        raw = raw if isinstance(raw, dict) else {}
        exif = raw.get('exif')
        exif = exif if isinstance(exif, dict) else {}
        result = {}
        for target, value in {
            'pixel_width': raw.get('width'), 'pixel_height': raw.get('height'),
            'file_size': photo.get('size'), 'focal_length_mm': exif.get('focalLength'),
            'aperture': exif.get('fNumber'), 'exposure_seconds': exif.get('exposure'), 'iso': exif.get('iso'),
            'latitude': exif.get('latitude'), 'longitude': exif.get('longitude'),
        }.items():
            if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
                if target in ('latitude', 'longitude'):
                    if abs(value) <= (90 if target == 'latitude' else 180):
                        result[target] = value
                elif value > 0:
                    result[target] = value
        for target, value in {'make': exif.get('make'), 'model': exif.get('model'), 'lens_model': exif.get('lens'), 'address': raw.get('place')}.items():
            if isinstance(value, str) and value.strip():
                result[target] = value.strip()[:300]
        taken = exif.get('takenAt')
        if isinstance(taken, str):
            try:
                parsed = datetime.strptime(taken, '%Y:%m:%d %H:%M:%S') if re.fullmatch(r'\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}', taken) else datetime.fromisoformat(taken.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
                result['taken_at'] = parsed.timestamp()
            except (ValueError, OverflowError):
                pass
        return {'status': 1 if result else 0, **result}

    @classmethod
    def token(cls, url):
        parsed = urlparse(url or '')
        if parsed.scheme not in ('http', 'https') or parsed.hostname != cls.HOST or parsed.port not in (None, 443):
            return None
        match = cls.SHARE_RE.fullmatch(parsed.path)
        return match.group(1).lower() if match else None

    @classmethod
    def parse(cls, url):
        token = cls.token(url)
        if not token:
            return {}
        canonical_url = f'https://{cls.HOST}/share/{token}'
        response = requests.get(
            f'https://{cls.API_HOST}/api/v1/sync/share/{token}',
            headers={'Accept': 'application/json'}, timeout=(3, 7), allow_redirects=False,
        )
        if response.status_code != 200 or 'application/json' not in response.headers.get('Content-Type', '').lower() or len(response.content) > 1024 * 1024:
            raise ValueError('stillfolio share unavailable')
        collection = response.json()
        if not isinstance(collection, dict):
            raise ValueError('invalid stillfolio share')
        photos = collection.get('photos')
        if not isinstance(photos, list):
            raise ValueError('invalid stillfolio photos')
        order = collection.get('photo_order') or []
        rank = {key: index for index, key in enumerate(order) if isinstance(key, str)}
        photos = sorted((photo for photo in photos if isinstance(photo, dict)), key=lambda photo: rank.get(photo.get('source_id'), len(rank)))
        images, image_metadata = [], []
        for photo in photos[:60]:
            path = photo.get('content_url')
            if not isinstance(path, str) or not re.fullmatch(rf'/api/v1/sync/share/{token}/assets/[0-9a-f-]{{36}}', path, re.I):
                continue
            images.append(f'https://{cls.API_HOST}{path}')
            image_metadata.append(cls.photo_metadata(photo))
        owner = collection.get('owner') or {}
        title = str(collection.get('name') or '留幅作品集').strip()[:255]
        author = str(owner.get('nickname') or '').strip()[:120] if isinstance(owner, dict) else ''
        description = str(collection.get('description') or '').strip()[:1000]
        return {
            'provider': 'stillfolio_collection', 'min_client_version': '2026.10.13.1', 'collection_id': token,
            'canonical_url': canonical_url, 'title': title, 'author': author,
            'description': description, 'cover_url': images[0] if images else '',
            'images': images, 'image_metadata': image_metadata, 'photo_count': len(photos),
        }
