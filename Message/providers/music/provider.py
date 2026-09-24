"""Resolvers for public music sharing links."""

import json
import re
from urllib.parse import parse_qs, urlparse

import requests


class MusicProvider:
    QQ_HOSTS = frozenset(('y.qq.com', 'c.y.qq.com', 'i.y.qq.com', 'c6.y.qq.com', 'y.qq.com'))
    KUGOU_HOSTS = frozenset(('kugou.com', 'www.kugou.com', 'm.kugou.com', 'h5.kugou.com', 't1.kugou.com', 't2.kugou.com', 't3.kugou.com', 't4.kugou.com'))
    QISHUI_HOSTS = frozenset(('qishui.douyin.com', 'music.douyin.com'))
    APPLE_HOSTS = frozenset(('music.apple.com',))
    KUWO_HOSTS = frozenset(('kuwo.cn', 'www.kuwo.cn', 'm.kuwo.cn'))
    HOSTS = QQ_HOSTS | KUGOU_HOSTS | QISHUI_HOSTS | APPLE_HOSTS | KUWO_HOSTS
    HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://y.qq.com/'}

    @classmethod
    def supports(cls, url):
        host = (urlparse((url or '').strip()).hostname or '').lower()
        return host in cls.HOSTS

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
        if host in cls.QISHUI_HOSTS:
            return cls._parse_qishui(url, html)
        if host in cls.APPLE_HOSTS:
            return cls._parse_apple(url, session)
        if host in cls.KUWO_HOSTS:
            return cls._parse_kuwo(url, html, session)
        return None

    @staticmethod
    def _json_after_marker(html, marker):
        if marker not in (html or ''):
            return {}
        payload = html.split(marker, 1)[1].lstrip()
        try:
            value, _ = json.JSONDecoder().raw_decode(payload)
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

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
            data = {}
        page_song = {}
        page_match = re.search(r'var\s+dataFromSmarty\s*=\s*(\[.*?\])\s*,?\s*//', html or '', re.DOTALL)
        if page_match:
            try:
                page_rows = json.loads(page_match.group(1))
                page_song = page_rows[0] if isinstance(page_rows, list) and page_rows else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                page_song = {}
        if not data:
            data = page_song
        audio_url = cls._https(data.get('play_url') or data.get('play_backup_url'))
        title = str(data.get('song_name') or data.get('audio_name') or '')[:255]
        artist = str(data.get('author_name') or '')[:120]
        if not title and (urlparse(url).hostname or '').lower() == 'h5.kugou.com':
            title = '酷狗音乐分享'
        if not title:
            return None
        return {
            'provider': 'kugou_music', 'song_id': song_hash.upper(),
            'title': title, 'artists': [artist] if artist else [],
            'album': str(data.get('album_name') or '')[:255], 'cover_url': cls._https(data.get('img')),
            'duration_ms': max(0, int(data.get('timelength') or 0)), 'audio_url': audio_url,
            'canonical_url': url, 'lyrics': {'original': str(data.get('lyrics') or '')[:100_000]},
        }

    @classmethod
    def _parse_qishui(cls, url, html):
        parsed = urlparse(url)
        track_id = (parse_qs(parsed.query).get('track_id') or [''])[0]
        router_data = cls._json_after_marker(html, '_ROUTER_DATA = ')
        track_page = ((router_data.get('loaderData') or {}).get('track_page') or {})
        track = track_page.get('audioWithLyricsOption') or {}
        track_id = str(track.get('track_id') or track_page.get('track_id') or track_id)
        if not track_id.isdigit():
            return None
        title = str(track.get('trackName') or '')[:255]
        artist = str(track.get('artistName') or '')[:120]
        if not title or title == '-':
            return None
        lyrics = track.get('lyrics') or track.get('lyric') or ''
        if isinstance(lyrics, dict):
            lyrics = lyrics.get('content') or lyrics.get('lyric') or ''
        duration = track.get('duration') or track.get('duration_ms') or 0
        try:
            duration_ms = int(float(duration))
            if duration_ms and duration_ms < 10_000:
                duration_ms *= 1000
        except (TypeError, ValueError):
            duration_ms = 0
        return {
            'provider': 'qishui_music', 'song_id': track_id, 'title': title,
            'artists': [artist] if artist and artist != '-' else [],
            'album': str(track.get('albumName') or '')[:255],
            'cover_url': cls._https(track.get('coverURL') or track.get('cover_url')),
            'duration_ms': max(0, duration_ms),
            'audio_url': cls._https(track.get('url') or track.get('playUrl') or track.get('play_url')),
            'canonical_url': f'https://music.douyin.com/qishui/share/track?track_id={track_id}',
            'lyrics': {'original': str(lyrics)[:100_000]} if lyrics else {},
        }

    @classmethod
    def _parse_apple(cls, url, session):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        song_id = (query.get('i') or [''])[0]
        if not song_id and '/song/' in parsed.path:
            match = re.search(r'/(\d+)(?:/)?$', parsed.path)
            song_id = match.group(1) if match else ''
        if not song_id.isdigit():
            return None
        storefront = next((part for part in parsed.path.split('/') if re.fullmatch(r'[a-zA-Z]{2}', part)), 'cn').lower()
        try:
            payload = session.get(
                'https://itunes.apple.com/lookup',
                params={'id': song_id, 'country': storefront, 'entity': 'song'},
                headers={**cls.HEADERS, 'Referer': 'https://music.apple.com/'}, timeout=(3, 8),
            ).json()
            results = payload.get('results') or []
            song = next((item for item in results if str(item.get('trackId') or '') == song_id), None)
        except (requests.RequestException, ValueError, TypeError):
            return None
        if not song:
            return None
        cover_url = str(song.get('artworkUrl100') or '').replace('100x100bb', '600x600bb')
        return {
            'provider': 'apple_music', 'song_id': song_id,
            'title': str(song.get('trackName') or '')[:255],
            'artists': [str(song.get('artistName'))[:120]] if song.get('artistName') else [],
            'album': str(song.get('collectionName') or '')[:255],
            'cover_url': cls._https(cover_url),
            'duration_ms': max(0, int(song.get('trackTimeMillis') or 0)),
            'audio_url': cls._https(song.get('previewUrl')),
            'canonical_url': str(song.get('trackViewUrl') or url).split('&uo=', 1)[0],
            'lyrics': {},
        }

    @staticmethod
    def _kuwo_html_value(html, key):
        match = re.search(rf'{re.escape(key)}\s*:\s*"((?:\\.|[^"\\])*)"', html or '')
        if not match:
            return ''
        try:
            return json.loads(f'"{match.group(1)}"')
        except (TypeError, ValueError, json.JSONDecodeError):
            return ''

    @staticmethod
    def _kuwo_lyrics(rows):
        lines = []
        for row in rows or []:
            if not isinstance(row, dict) or not row.get('lineLyric'):
                continue
            try:
                seconds = float(row.get('time') or 0)
            except (TypeError, ValueError):
                seconds = 0
            minutes, remainder = divmod(max(0, seconds), 60)
            lines.append(f'[{int(minutes):02d}:{remainder:05.2f}]{row["lineLyric"]}')
        return '\n'.join(lines)

    @classmethod
    def _parse_kuwo(cls, url, html, session):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        match = re.search(r'/(?:play_detail|song)/(?:MUSIC_)?(\d+)', parsed.path, re.IGNORECASE)
        song_id = match.group(1) if match else (query.get('mid') or query.get('musicId') or query.get('musicid') or [''])[0]
        song_id = str(song_id).removeprefix('MUSIC_')
        if not song_id.isdigit():
            return None
        detail = {}
        try:
            payload = session.get(
                'https://www.kuwo.cn/newh5/singles/songinfoandlrc', params={'musicId': song_id},
                headers={**cls.HEADERS, 'Referer': url}, timeout=(3, 8),
            ).json()
            detail = payload.get('data') or {}
        except (requests.RequestException, ValueError, TypeError):
            pass
        song = detail.get('songinfo') or detail.get('songInfo') or detail
        title = str(song.get('songName') or song.get('name') or cls._kuwo_html_value(html, 'name'))[:255]
        artist = str(song.get('artist') or song.get('artistName') or cls._kuwo_html_value(html, 'artist'))[:120]
        album = str(song.get('album') or song.get('albumName') or cls._kuwo_html_value(html, 'album'))[:255]
        cover_url = song.get('pic') or song.get('pic500') or song.get('pic120') or cls._kuwo_html_value(html, 'pic120')
        try:
            audio_payload = session.get(
                'https://antiserver.kuwo.cn/anti.s',
                params={'type': 'convert_url3', 'rid': f'MUSIC_{song_id}', 'format': 'mp3', 'response': 'url'},
                headers={**cls.HEADERS, 'Referer': url}, timeout=(3, 8),
            ).json()
            audio_url = cls._https(audio_payload.get('url') or (audio_payload.get('data') or {}).get('url'))
        except (requests.RequestException, ValueError, TypeError):
            audio_url = ''
        if not title:
            title_match = re.search(r'<title>\s*([^<_]+)', html or '', re.IGNORECASE)
            title = (title_match.group(1).strip() if title_match else '')[:255]
        if not title:
            return None
        lyrics = cls._kuwo_lyrics(detail.get('lrclist') or detail.get('lrcList'))
        return {
            'provider': 'kuwo_music', 'song_id': song_id, 'title': title,
            'artists': [artist] if artist else [], 'album': album,
            'cover_url': cls._https(cover_url), 'duration_ms': 0, 'audio_url': audio_url,
            'canonical_url': f'https://www.kuwo.cn/play_detail/{song_id}',
            'lyrics': {'original': lyrics[:100_000]} if lyrics else {},
        }
