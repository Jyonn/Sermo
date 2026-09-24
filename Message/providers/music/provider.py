"""Resolvers for public QQ Music and KuGou song links."""

import re
from urllib.parse import parse_qs, urlparse

import requests


class MusicProvider:
    QQ_HOSTS = frozenset(('y.qq.com', 'c.y.qq.com', 'i.y.qq.com', 'c6.y.qq.com', 'y.qq.com'))
    KUGOU_HOSTS = frozenset(('kugou.com', 'www.kugou.com', 'm.kugou.com', 't1.kugou.com', 't2.kugou.com', 't3.kugou.com', 't4.kugou.com'))
    HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://y.qq.com/'}

    @classmethod
    def supports(cls, url):
        host = (urlparse((url or '').strip()).hostname or '').lower()
        return host in cls.QQ_HOSTS or host in cls.KUGOU_HOSTS

    @staticmethod
    def _https(value):
        value = str(value or '').strip().replace('http://', 'https://', 1)
        return value if urlparse(value).scheme == 'https' else ''

    @classmethod
    def parse(cls, url, html='', session=None):
        host = (urlparse((url or '').strip()).hostname or '').lower()
        session = session or requests.Session()
        if host in cls.QQ_HOSTS:
            return cls._parse_qq(url, html, session)
        if host in cls.KUGOU_HOSTS:
            return cls._parse_kugou(url, html, session)
        return None

    @classmethod
    def _parse_qq(cls, url, html, session):
        parsed = urlparse(url)
        match = re.search(r'/songDetail/([A-Za-z0-9]+)', parsed.path)
        song_mid = match.group(1) if match else parse_qs(parsed.query).get('songmid', [''])[0]
        if not song_mid:
            match = re.search(r'"songmid"\s*:\s*"([A-Za-z0-9]+)"', html or '')
            song_mid = match.group(1) if match else ''
        if not re.fullmatch(r'[A-Za-z0-9]{8,32}', song_mid):
            return None
        try:
            detail = session.get(
                'https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg',
                params={'songmid': song_mid, 'format': 'json'}, headers=cls.HEADERS, timeout=(3, 8),
            ).json()
            song = (detail.get('data') or [])[0]
            file_mid = ((song.get('file') or {}).get('media_mid') or song_mid)
            filename = f'C400{file_mid}.m4a'
            vkey_data = session.get(
                'https://c.y.qq.com/base/fcgi-bin/fcg_music_express_mobile3.fcg',
                params={'format': 'json', 'platform': 'yqq', 'cid': '205361747', 'songmid': song_mid, 'filename': filename, 'guid': '126548448'},
                headers=cls.HEADERS, timeout=(3, 8),
            ).json()
            item = (vkey_data.get('data', {}).get('items') or [{}])[0]
            vkey = item.get('vkey') or ''
            audio_url = cls._https(f"https://isure.stream.qqmusic.qq.com/{item.get('filename') or filename}?guid=126548448&vkey={vkey}&uin=0&fromtag=66") if vkey else ''
            if not audio_url:
                musicu = session.post(
                    'https://u.y.qq.com/cgi-bin/musicu.fcg',
                    json={
                        'comm': {'uin': '0', 'format': 'json', 'ct': 24, 'cv': 0},
                        'req_0': {'module': 'vkey.GetVkeyServer', 'method': 'CgiGetVkey', 'param': {
                            'guid': '126548448', 'songmid': [song_mid], 'songtype': [0],
                            'uin': '0', 'loginflag': 1, 'platform': '20',
                        }},
                    },
                    headers={**cls.HEADERS, 'Content-Type': 'application/json'}, timeout=(3, 8),
                ).json()
                playback = musicu.get('req_0', {}).get('data', {})
                media = (playback.get('midurlinfo') or [{}])[0]
                purl = media.get('purl') or ''
                hosts = playback.get('sip') or []
                audio_url = cls._https(f"{hosts[0]}{purl}") if hosts and purl else ''
            lyric_data = session.get(
                'https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg',
                params={'songmid': song_mid, 'format': 'json', 'nobase64': 1}, headers=cls.HEADERS, timeout=(3, 8),
            ).json()
        except (requests.RequestException, ValueError, TypeError, IndexError, KeyError):
            return None
        album = song.get('album') or {}
        album_mid = album.get('mid') or ''
        return {
            'provider': 'qq_music', 'song_id': song_mid, 'title': str(song.get('name') or '')[:255],
            'artists': [str(item.get('name'))[:120] for item in song.get('singer') or [] if item.get('name')],
            'album': str(album.get('name') or '')[:255],
            'cover_url': f'https://y.gtimg.cn/music/photo_new/T002R500x500M000{album_mid}.jpg' if album_mid else '',
            'duration_ms': max(0, int(song.get('interval') or 0) * 1000), 'audio_url': audio_url,
            'canonical_url': f'https://y.qq.com/n/ryqq/songDetail/{song_mid}',
            'lyrics': {'original': str(lyric_data.get('lyric') or '')[:100_000], 'translation': str(lyric_data.get('trans') or '')[:100_000]},
        }

    @classmethod
    def _parse_kugou(cls, url, html, session):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        song_hash = (query.get('hash') or [''])[0]
        album_id = (query.get('album_id') or [''])[0]
        if not song_hash:
            match = re.search(r'"(?:hash|audio_id)"\s*:\s*"([A-Fa-f0-9]{32})"', html or '')
            song_hash = match.group(1) if match else ''
        if not album_id:
            match = re.search(r'"album_id"\s*:\s*"?(\d+)"?', html or '')
            album_id = match.group(1) if match else ''
        if not re.fullmatch(r'[A-Fa-f0-9]{32}', song_hash):
            return None
        try:
            payload = session.get(
                'https://www.kugou.com/yy/index.php',
                params={'r': 'play/getdata', 'hash': song_hash.upper(), 'album_id': album_id},
                headers={**cls.HEADERS, 'Referer': url}, timeout=(3, 8),
            ).json()
            data = payload.get('data') or {}
        except (requests.RequestException, ValueError, TypeError):
            return None
        audio_url = cls._https(data.get('play_url') or data.get('play_backup_url'))
        if not data:
            return None
        return {
            'provider': 'kugou_music', 'song_id': song_hash.upper(),
            'title': str(data.get('song_name') or data.get('audio_name') or '')[:255],
            'artists': [str(data.get('author_name'))[:120]] if data.get('author_name') else [],
            'album': str(data.get('album_name') or '')[:255], 'cover_url': cls._https(data.get('img')),
            'duration_ms': max(0, int(data.get('timelength') or 0)), 'audio_url': audio_url,
            'canonical_url': url, 'lyrics': {'original': str(data.get('lyrics') or '')[:100_000]},
        }
