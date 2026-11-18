"""JustOneAPI adapter. Credentials and upstream error URLs never leave this module."""
import os
import re
from urllib.parse import urlparse, parse_qs

import requests


class JustOneProvider:
    VERSION = 1
    DY = frozenset(('douyin.com', 'www.douyin.com', 'v.douyin.com', 'iesdouyin.com', 'www.iesdouyin.com'))
    XHS = frozenset(('xiaohongshu.com', 'www.xiaohongshu.com', 'xhslink.com', 'www.xhslink.com', 'xhslink.cn', 'www.xhslink.cn'))

    @classmethod
    def supports(cls, url):
        return urlparse(url).hostname in cls.DY | cls.XHS

    @staticmethod
    def request(path, **params):
        token = os.environ.get('JUSTONEAPI_TOKEN', '')
        if not token:
            raise ValueError('social_provider_unconfigured')
        try:
            response = requests.get('https://api.justoneapi.com/api/' + path,
                                    params={'token': token, **params}, timeout=(5, 65))
            response.raise_for_status()
            body = response.json()
            if body.get('code') != 0:
                raise ValueError('social_provider_failed')
            return body['data']
        except (requests.RequestException, KeyError, TypeError):
            raise ValueError('social_provider_failed') from None

    @staticmethod
    def media(url):
        if not isinstance(url, str):
            return ''
        p = urlparse(url)
        domains = ('douyinvod.com', 'douyincdn.com', 'bytecdn.cn', 'snssdk.com', 'amemv.com', 'zjcdn.com', 'douyinpic.com', 'byteimg.com', 'xhscdn.com', 'rednotecdn.com', 'xhsimg.com')
        if p.scheme in ('http', 'https') and not p.username and any((p.hostname or '') == d or (p.hostname or '').endswith('.' + d) for d in domains):
            return p._replace(scheme='https').geturl()
        return ''

    @classmethod
    def parse(cls, url):
        dy = urlparse(url).hostname in cls.DY
        platform = 'douyin' if dy else 'xiaohongshu'
        if urlparse(url).hostname in ('v.douyin.com', 'xhslink.com', 'www.xhslink.com', 'xhslink.cn', 'www.xhslink.cn'):
            url = cls.request(platform + '/share-url-transfer/v1', shareUrl=url).get('redirect_url', '')
        if urlparse(url).hostname not in (cls.DY if dy else cls.XHS):
            raise ValueError('social_invalid_redirect')
        match = re.search(r'/(?:video|note|slides)/([0-9]{15,22})', url) if dy else re.search(r'/(?:explore|discovery/item)/([a-fA-F0-9]{24})', url)
        ident = match.group(1) if match else (parse_qs(urlparse(url).query).get('modal_id', [''])[0] if dy else '')
        if not ident:
            raise ValueError('social_invalid_id')
        data = cls.request(platform + ('/get-video-detail/v2' if dy else '/get-note-detail/v1'), **({'videoId': ident} if dy else {'noteId': ident}))
        note = data.get('aweme_detail', {}) if dy else next((n for group in data if isinstance(group, dict) for n in group.get('note_list', []) if n.get('id') == ident), {})
        if not note or (dy and str(note.get('aweme_id')) != ident):
            raise ValueError('social_content_unavailable')
        author = note.get('author' if dy else 'user') or {}
        video = note.get('video') or {}
        images = []
        for item in note.get('images' if dy else 'images_list') or []:
            candidates = item.get('url_list', []) if dy else [item.get('url'), item.get('original')]
            image = next((cls.media(u) for u in candidates if cls.media(u)), '')
            if image:
                images.append(image)
        streams = video.get('bit_rate', []) if dy else ((video.get('media') or {}).get('stream') or {}).get('h264', [])
        urls = []
        for stream in streams:
            if dy and (stream.get('is_h265') or stream.get('is_bytevc1')):
                continue
            urls.extend((stream.get('play_addr') or {}).get('url_list', []) if dy else [stream.get('master_url')])
        if dy:
            urls.extend((video.get('play_addr') or {}).get('url_list', []))
        video_url = next((cls.media(u) for u in urls if cls.media(u)), '')
        is_video = not images if dy else note.get('type') == 'video'
        cover = next((cls.media(u) for u in (video.get('cover') or {}).get('url_list', []) if cls.media(u)), '') if dy else ''
        title = str(note.get('item_title' if dy else 'title') or note.get('desc') or '')[:255]
        media = dict(provider=platform + ('_video' if is_video else '_gallery'), title=title,
                     author=str(author.get('nickname') or author.get('name') or '')[:120],
                     description=str(note.get('desc') or '')[:1000],
                     canonical_url=f'https://www.douyin.com/{"video" if is_video else "note"}/{ident}' if dy else f'https://www.xiaohongshu.com/explore/{ident}',
                     cover_url=cover or (images[0] if images else ''), adapter='justone', adapter_version=cls.VERSION)
        media['video_id' if dy else 'note_id'] = ident
        if is_video:
            if not video_url:
                # A cover is not a gallery and audio is not a video.
                raise ValueError('social_video_unavailable')
            media.update(video_url=video_url, width=video.get('width', 0), height=video.get('height', 0), duration_ms=video.get('duration', 0))
        else:
            if not images:
                raise ValueError('social_content_unavailable')
            media['images'] = images[:100]
        return dict(url=media['canonical_url'], title=title, description=media['description'] or media['author'],
                    image_url=media['cover_url'], site_name='抖音' if dy else '小红书', favicon_url='', provider_data=media)
