"""Resolve public Douyin videos and photo notes from Douyin-owned sources."""

import json
import re
from urllib.parse import parse_qs, urljoin, urlparse

import requests


class DouyinProvider:
    FEED_URL = 'https://api5-normal-c-hl.amemv.com/aweme/v1/feed/'
    BROWSER_HEADERS = {
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/138.0.0.0 Safari/537.36'
        ),
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    MAX_GALLERY_IMAGES = 100
    HOSTS = frozenset(('douyin.com', 'www.douyin.com', 'v.douyin.com', 'iesdouyin.com', 'www.iesdouyin.com'))
    VIDEO_HOSTS = ('douyinvod.com', 'douyincdn.com', 'bytecdn.cn', 'snssdk.com', 'amemv.com', 'zjcdn.com')
    IMAGE_HOSTS = ('douyinpic.com', 'byteimg.com')

    def __init__(self, session=None):
        self.session = session or requests.Session()

    @classmethod
    def supports(cls, url):
        parsed = urlparse((url or '').strip())
        return parsed.scheme == 'https' and (parsed.hostname or '').lower() in cls.HOSTS

    @classmethod
    def video_id_from_url(cls, url):
        if not cls.supports(url):
            return None
        parsed = urlparse(url)
        match = re.search(r'/(?:aweme/detail/|(?:share/)?video/|(?:share/)?(?:note|slides)/)(\d{10,25})', parsed.path)
        if match:
            return match.group(1)
        query = parse_qs(parsed.query)
        for key in ('modal_id', 'aweme_id', 'video_id', 'note_id'):
            value = query.get(key, [''])[0]
            if re.fullmatch(r'\d{10,25}', value):
                return value
        return None

    @staticmethod
    def _trusted_url(value, hosts):
        if not isinstance(value, str):
            return ''
        parsed = urlparse(value.strip())
        host = (parsed.hostname or '').lower()
        if parsed.scheme != 'https' or not parsed.path or parsed.path == '/':
            return ''
        return value.strip() if any(host == domain or host.endswith('.' + domain) for domain in hosts) else ''

    @classmethod
    def _first_url(cls, value, hosts):
        urls = (value.get('url_list') or []) if isinstance(value, dict) else []
        for url in urls if isinstance(urls, list) else []:
            trusted = cls._trusted_url(url, hosts)
            if trusted:
                return trusted
        return ''

    def _resolve_url(self, url):
        for _ in range(4):
            if self.video_id_from_url(url):
                return url
            try:
                response = self.session.get(
                    url, headers={**self.BROWSER_HEADERS, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'},
                    timeout=(3, 5), allow_redirects=False, stream=True,
                )
                try:
                    location = response.headers.get('Location') if 300 <= response.status_code < 400 else None
                finally:
                    response.close()
            except requests.RequestException:
                return None
            if not location:
                return None
            url = urljoin(url, location)
            if not self.supports(url):
                return None
        return url if self.video_id_from_url(url) else None

    def _video(self, video_id):
        try:
            response = self.session.get(
                self.FEED_URL, params={'aweme_id': video_id, 'aid': '1128'},
                headers={**self.BROWSER_HEADERS, 'Accept': 'application/json'},
                timeout=(3, 10),
            )
            try:
                response.raise_for_status()
                payload = response.json()
            finally:
                response.close()
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        # The feed includes recommendations: never use an item other than the requested work.
        item = next((entry for entry in payload.get('aweme_list') or []
                     if isinstance(entry, dict) and str(entry.get('aweme_id')) == video_id), None)
        if not item:
            return None
        video = item.get('video') or {}
        if not isinstance(video, dict):
            return None
        h264 = video.get('play_addr_h264') or {}
        if not h264:
            candidates = [entry for entry in video.get('bit_rate') or []
                          if isinstance(entry, dict) and entry.get('is_h265') in (0, '0')]
            if candidates:
                h264 = max(candidates, key=lambda entry: entry.get('bit_rate') or 0).get('play_addr') or {}
        if not h264 and video.get('is_h265') in (0, '0', None):
            h264 = video.get('play_addr') or {}
        video_url = self._first_url(h264, self.VIDEO_HOSTS)
        if not video_url:
            return None
        try:
            width = max(0, int(h264.get('width') or video.get('width') or 0))
            height = max(0, int(h264.get('height') or video.get('height') or 0))
            duration = max(0, int(video.get('duration') or 0))
        except (TypeError, ValueError):
            width = height = duration = 0
        author = item.get('author') or {}
        return {
            'provider': 'douyin_video', 'video_id': video_id,
            'title': str(item.get('desc') or '')[:255],
            'author': str(author.get('nickname') or '')[:120] if isinstance(author, dict) else '',
            'canonical_url': f'https://www.douyin.com/video/{video_id}',
            'video_url': video_url,
            'cover_url': self._first_url(video.get('cover') or {}, self.IMAGE_HOSTS),
            'duration_ms': duration, 'width': width, 'height': height,
            'qualities': [{'label': f'{height}p', 'height': height, 'width': width,
                           'bitrate': 0, 'url': video_url}],
        }

    def _gallery(self, video_id):
        canonical_url = f'https://www.douyin.com/note/{video_id}'
        try:
            response = self.session.get(
                canonical_url, headers={**self.BROWSER_HEADERS, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'},
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
                    if size > 1024 * 1024:
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
                trusted = self._trusted_url(value, self.IMAGE_HOSTS)
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
                'canonical_url': canonical_url, 'cover_url': images[0],
                'images': images[:self.MAX_GALLERY_IMAGES],
            }
        return None

    def parse(self, url):
        normalized_url = (url or '').strip()
        if not self.supports(normalized_url):
            return None
        resolved = self._resolve_url(normalized_url)
        if not resolved:
            return None
        video_id = self.video_id_from_url(resolved)
        if not video_id:
            return None
        path = urlparse(resolved).path
        if '/note/' in path or '/slides/' in path:
            return self._gallery(video_id)
        return self._video(video_id) or self._gallery(video_id)
