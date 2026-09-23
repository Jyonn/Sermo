"""Sermo adapter for DLWangSan/douyin_parse.

The upstream project is intentionally isolated behind this provider because its
request parameters and signing algorithms are expected to change with Douyin.
Upstream commit: 0896c74d1e9368af8ad0b85449a8039b1b3010bd
"""

import os
import re
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests

from .abogus import ABogus
from .xbogus import XBogus


class DouyinProvider:
    HOSTS = frozenset(('douyin.com', 'www.douyin.com', 'v.douyin.com', 'iesdouyin.com'))
    MEDIA_HOSTS = ('douyinvod.com', 'douyincdn.com', 'bytecdn.cn', 'snssdk.com', 'amemv.com')
    USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    BASE_PARAMS = {
        'device_platform': 'webapp', 'aid': '6383', 'channel': 'channel_pc_web',
        'pc_client_type': '1', 'version_code': '190500', 'version_name': '19.5.0',
        'cookie_enabled': 'true', 'browser_language': 'zh-CN', 'browser_platform': 'Win32',
        'browser_name': 'Edge', 'browser_online': 'true', 'engine_name': 'Blink',
        'os_name': 'Windows', 'os_version': '10', 'platform': 'PC',
        'screen_width': '1920', 'screen_height': '1080',
    }

    def __init__(self, cookie=None, session=None):
        self.cookie = cookie if cookie is not None else os.environ.get('DOUYIN_COOKIE', '').strip()
        self.session = session or requests.Session()
        self.abogus = ABogus()
        self.xbogus = XBogus(self.USER_AGENT)

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

    def _headers(self, video_id):
        headers = {
            'User-Agent': self.USER_AGENT, 'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': f'https://www.douyin.com/video/{video_id}',
            'Origin': 'https://www.douyin.com', 'Sec-Fetch-Site': 'same-site',
            'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty',
        }
        if self.cookie:
            headers['Cookie'] = self.cookie
        return headers

    def _request_detail(self, video_id):
        api_url = 'https://www.douyin.com/aweme/v1/web/aweme/detail/'
        params = {**self.BASE_PARAMS, 'aweme_id': video_id}
        encoded = urlencode(params)
        signed = f'{api_url}?{encoded}&a_bogus={quote(self.abogus.get_value(params), safe="")}'
        response = self.session.get(signed, headers=self._headers(video_id), timeout=(3, 12))
        if response.status_code == 200 and response.content:
            payload = response.json()
            if isinstance(payload, dict) and payload.get('status_code') == 0 and payload.get('aweme_detail'):
                return payload['aweme_detail']
        xbogus_params, _signature, _ua = self.xbogus.get_xbogus(encoded)
        response = self.session.get(f'{api_url}?{xbogus_params}', headers=self._headers(video_id), timeout=(3, 12))
        if response.status_code != 200 or not response.content:
            return None
        payload = response.json()
        return payload.get('aweme_detail') if isinstance(payload, dict) and payload.get('status_code') == 0 else None

    @classmethod
    def _media_url(cls, video):
        candidates = []
        for quality in video.get('bit_rate') or []:
            if isinstance(quality, dict):
                candidates.extend((quality.get('play_addr') or {}).get('url_list') or [])
        candidates.extend((video.get('play_addr') or {}).get('url_list') or [])
        uri = (video.get('play_addr') or {}).get('uri')
        if uri:
            candidates.append(f'https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio=1080p&line=0')
        for candidate in candidates:
            parsed = urlparse(candidate) if isinstance(candidate, str) else None
            host = (parsed.hostname or '').lower() if parsed else ''
            if parsed and parsed.scheme == 'https' and any(host == domain or host.endswith('.' + domain) for domain in cls.MEDIA_HOSTS):
                return candidate.replace('playwm', 'play')
        return ''

    def parse(self, url, video_id=None):
        video_id = video_id or self.video_id_from_url(url)
        if not video_id:
            return None
        detail = self._request_detail(video_id)
        if not detail:
            return None
        video = detail.get('video') or {}
        video_url = self._media_url(video)
        if not video_url:
            return None
        covers = (video.get('cover') or {}).get('url_list') or []
        return {
            'provider': 'douyin_video', 'video_id': str(detail.get('aweme_id') or video_id),
            'title': str(detail.get('desc') or '')[:255], 'canonical_url': f'https://www.douyin.com/video/{video_id}',
            'video_url': video_url, 'cover_url': covers[0] if covers else '',
            'width': max(0, int(video.get('width') or 0)), 'height': max(0, int(video.get('height') or 0)),
        }
