"""Douyin video resolver backed by douyinsaver.com."""

import re
from urllib.parse import parse_qs, urlparse

import requests


class DouyinProvider:
    API_URL = 'https://api.douyinsaver.com/api/parse'
    HOSTS = frozenset(('douyin.com', 'www.douyin.com', 'v.douyin.com', 'iesdouyin.com'))
    MEDIA_HOSTS = (
        'douyinvod.com', 'douyincdn.com', 'bytecdn.cn', 'snssdk.com',
        'amemv.com', 'zjcdn.com',
    )

    def __init__(self, session=None):
        self.session = session or requests.Session()

    @classmethod
    def supports(cls, url):
        return (urlparse((url or '').strip()).hostname or '').lower() in cls.HOSTS

    @classmethod
    def video_id_from_url(cls, url):
        parsed = urlparse((url or '').strip())
        if (parsed.hostname or '').lower() not in cls.HOSTS:
            return None
        match = re.search(r'/(?:aweme/detail/|video/|note/)(\d{10,25})', parsed.path)
        if match:
            return match.group(1)
        query = parse_qs(parsed.query)
        for key in ('modal_id', 'aweme_id', 'video_id', 'note_id'):
            value = query.get(key, [''])[0]
            if re.fullmatch(r'\d{10,25}', value):
                return value
        return None

    @classmethod
    def _trusted_media_url(cls, value):
        if not isinstance(value, str):
            return ''
        parsed = urlparse(value.strip())
        host = (parsed.hostname or '').lower()
        if parsed.scheme != 'https' or not parsed.path or parsed.path == '/':
            return ''
        if not any(host == domain or host.endswith('.' + domain) for domain in cls.MEDIA_HOSTS):
            return ''
        return value.strip()

    @classmethod
    def _qualities(cls, values):
        best_by_height = {}
        for value in values if isinstance(values, list) else []:
            if not isinstance(value, dict):
                continue
            url = cls._trusted_media_url(value.get('url'))
            if not url:
                continue
            try:
                height = max(0, int(value.get('height') or 0))
                width = max(0, int(value.get('width') or 0))
                bitrate = max(0, int(value.get('bitrate') or 0))
            except (TypeError, ValueError):
                continue
            if not height:
                continue
            quality = {
                'label': str(value.get('label') or f'{height}p')[:32],
                'height': height,
                'width': width,
                'bitrate': bitrate,
                'url': url,
            }
            previous = best_by_height.get(height)
            if previous is None or (bitrate, width) > (previous['bitrate'], previous['width']):
                best_by_height[height] = quality
        return sorted(best_by_height.values(), key=lambda item: (item['height'], item['width'], item['bitrate']), reverse=True)

    def parse(self, url, video_id=None):
        normalized_url = (url or '').strip()
        if not self.supports(normalized_url):
            return None
        try:
            response = self.session.post(
                self.API_URL,
                json={'url': normalized_url},
                headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
                timeout=(3, 20),
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(payload, dict):
            return None

        resolved_id = str(payload.get('aweme_id') or video_id or self.video_id_from_url(normalized_url) or '')
        if not re.fullmatch(r'\d{10,25}', resolved_id):
            return None
        qualities = self._qualities(payload.get('qualities'))
        if not qualities:
            return None
        selected = qualities[0]
        try:
            duration_ms = max(0, int(payload.get('duration') or 0))
        except (TypeError, ValueError):
            duration_ms = 0
        return {
            'provider': 'douyin_video',
            'video_id': resolved_id,
            'title': str(payload.get('title') or '')[:255],
            'author': str(payload.get('author') or '')[:120],
            'canonical_url': f'https://www.douyin.com/video/{resolved_id}',
            'video_url': selected['url'],
            'cover_url': str(payload.get('cover') or '').strip(),
            'duration_ms': duration_ms,
            'width': selected['width'],
            'height': selected['height'],
            'qualities': qualities,
        }
