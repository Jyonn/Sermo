"""Extract public Xiaohongshu note media from its server-rendered state."""

import json
import re
from urllib.parse import urlparse


class XiaohongshuProvider:
    HOSTS = frozenset(('xiaohongshu.com', 'www.xiaohongshu.com', 'xhslink.com', 'www.xhslink.com'))
    IMAGE_HOSTS = ('xhscdn.com', 'xhsimg.com')
    VIDEO_HOSTS = ('xhscdn.com',)

    @classmethod
    def supports(cls, url):
        return (urlparse(url or '').hostname or '').lower() in cls.HOSTS

    @staticmethod
    def _media_url(value, hosts):
        if not isinstance(value, str):
            return ''
        value = value.replace('\\u0026', '&').strip()
        parsed = urlparse(value)
        host = (parsed.hostname or '').lower()
        if parsed.scheme != 'https' or not parsed.path or not any(host == domain or host.endswith('.' + domain) for domain in hosts):
            return ''
        return value

    @staticmethod
    def _initial_state(html):
        marker = 'window.__INITIAL_STATE__'
        start = html.find(marker)
        if start < 0:
            return {}
        start = html.find('{', start + len(marker))
        if start < 0:
            return {}
        decoder = json.JSONDecoder()
        try:
            source = re.sub(r'(?<=:)\s*undefined(?=\s*[,}])', 'null', html[start:])
            state, _ = decoder.raw_decode(source)
            return state if isinstance(state, dict) else {}
        except ValueError:
            return {}

    @classmethod
    def parse(cls, url, html):
        if not cls.supports(url):
            return {}
        match = re.search(r'/(?:explore/|discovery/item/)([0-9a-f]{24})', urlparse(url).path, re.I)
        if not match:
            return {}
        note_id = match.group(1)
        state = cls._initial_state(html)
        details = ((state.get('note') or {}).get('noteDetailMap') or {}) if isinstance(state.get('note'), dict) else {}
        detail = details.get(note_id) or {}
        note = detail.get('note', detail) if isinstance(detail, dict) else {}
        if not isinstance(note, dict):
            return {}
        title = str(note.get('title') or note.get('desc') or '')[:255]
        user = note.get('user') or {}
        author = str(user.get('nickname') or '')[:120] if isinstance(user, dict) else ''
        images = []
        for image in note.get('imageList') or []:
            if not isinstance(image, dict):
                continue
            info = image.get('infoList') or []
            candidate = image.get('urlDefault') or image.get('urlPre') or image.get('url') or (info[0].get('url') if info and isinstance(info[0], dict) else '')
            media = cls._media_url(candidate, cls.IMAGE_HOSTS)
            if media and media not in images:
                images.append(media)
        cover = images[0] if images else ''
        canonical_url = url
        if note.get('type') == 'video':
            video = note.get('video') or {}
            media = video.get('media') or {} if isinstance(video, dict) else {}
            streams = media.get('stream') or {} if isinstance(media, dict) else {}
            h264 = streams.get('h264') or [] if isinstance(streams, dict) else []
            first = h264[0] if h264 and isinstance(h264[0], dict) else {}
            video_url = cls._media_url(first.get('masterUrl'), cls.VIDEO_HOSTS)
            if video_url:
                return {'provider': 'xiaohongshu_video', 'note_id': note_id, 'title': title,
                        'author': author, 'canonical_url': canonical_url, 'cover_url': cover,
                        'video_url': video_url, 'duration_ms': first.get('duration') or 0}
        if images:
            return {'provider': 'xiaohongshu_gallery', 'note_id': note_id, 'title': title,
                    'author': author, 'canonical_url': canonical_url, 'cover_url': cover,
                    'images': images[:30], 'description': str(note.get('desc') or '')[:1000]}
        return {}
