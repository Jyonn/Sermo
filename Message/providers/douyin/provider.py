"""Douyin media resolver with a public note-page gallery fallback."""

import json
import re
from urllib.parse import parse_qs, urlparse

import requests


class DouyinProvider:
    API_URL = 'https://api.douyinsaver.com/api/parse'
    HOSTS = frozenset(('douyin.com', 'www.douyin.com', 'v.douyin.com', 'iesdouyin.com', 'www.iesdouyin.com'))
    MEDIA_HOSTS = (
        'douyinvod.com', 'douyincdn.com', 'bytecdn.cn', 'snssdk.com',
        'amemv.com', 'zjcdn.com', 'douyinpic.com', 'byteimg.com',
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
        match = re.search(r'/(?:aweme/detail/|(?:share/)?video/|(?:share/)?note/)(\d{10,25})', parsed.path)
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

    def _public_gallery(self, video_id):
        canonical_url = f'https://www.douyin.com/note/{video_id}'
        try:
            response = self.session.get(
                canonical_url,
                headers={'User-Agent': 'Googlebot', 'Accept': 'text/html'},
                timeout=(3, 10), allow_redirects=False, stream=True,
            )
            try:
                response.raise_for_status()
                if 'text/html' not in (response.headers.get('Content-Type') or '').lower():
                    return None
                chunks = []
                size = 0
                for chunk in response.iter_content(chunk_size=8192):
                    size += len(chunk)
                    if size > 512 * 1024:
                        return None
                    chunks.append(chunk)
            finally:
                response.close()
        except requests.RequestException:
            return None

        html = b''.join(chunks).decode('utf-8', errors='replace')
        for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(data, dict) or data.get('@type') != 'article':
                continue
            images = []
            for value in data.get('image') if isinstance(data.get('image'), list) else []:
                trusted = self._trusted_media_url(value)
                if trusted and trusted not in images:
                    images.append(trusted)
            if not images:
                continue
            author = data.get('author') or {}
            return {
                'provider': 'douyin_gallery', 'video_id': video_id,
                'title': str(data.get('headline') or '')[:255],
                'author': str(author.get('name') or '')[:120] if isinstance(author, dict) else '',
                'description': str(data.get('description') or '')[:1000],
                'canonical_url': canonical_url, 'cover_url': images[0], 'images': images[:30],
            }
        return None

    def parse(self, url, video_id=None):
        normalized_url = (url or '').strip()
        if not self.supports(normalized_url):
            return None
        note_id = self.video_id_from_url(normalized_url) if '/note/' in urlparse(normalized_url).path else None
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
            return self._public_gallery(note_id) if note_id else None
        if not isinstance(payload, dict):
            return self._public_gallery(note_id) if note_id else None

        resolved_id = str(payload.get('aweme_id') or video_id or self.video_id_from_url(normalized_url) or '')
        if not re.fullmatch(r'\d{10,25}', resolved_id):
            return self._public_gallery(note_id) if note_id else None
        qualities = self._qualities(payload.get('qualities'))
        images = []
        raw_images = payload.get('images') or payload.get('image_urls') or payload.get('image_list') or []
        for item in raw_images if isinstance(raw_images, list) else []:
            candidate = item if isinstance(item, str) else (item.get('url') or item.get('image_url') or '') if isinstance(item, dict) else ''
            if isinstance(candidate, dict):
                urls = candidate.get('url_list') or []
                candidate = urls[0] if isinstance(urls, list) and urls else ''
            trusted = self._trusted_media_url(candidate)
            if trusted and trusted not in images:
                images.append(trusted)
        if not qualities and images:
            return {
                'provider': 'douyin_gallery', 'video_id': resolved_id,
                'title': str(payload.get('title') or '')[:255],
                'author': str(payload.get('author') or '')[:120],
                'canonical_url': f'https://www.douyin.com/note/{resolved_id}',
                'cover_url': images[0], 'images': images[:30],
            }
        if not qualities:
            return self._public_gallery(note_id) if note_id else None
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
