"""Read public Pocklo shares without retaining signed download URLs."""
import json
import re
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime


HOST = 'pocklo.jyonn.space'
API = 'https://disk.6-79.cn/api'
TOKEN = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
SHARE_PATH = re.compile(rf'/share/({TOKEN})/?', re.I)
RESOURCE_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')


def share_token(url):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    match = SHARE_PATH.fullmatch(parsed.path)
    if (match and parsed.scheme == 'https' and parsed.netloc == HOST
            and not parsed.query and not parsed.fragment):
        return match[1].lower()


class SongUnavailable(ValueError):
    def __init__(self, status):
        self.status = status


def public_body(path, params=None):
    # The override is available only in explicit local test configurations.
    base = getattr(settings, 'POCKLO_TEST_API', API) if settings.DEBUG else API
    with requests.get(base + path, params=params, timeout=(3, 6),
                      stream=True, allow_redirects=False) as response:
        if response.status_code in (404, 410):
            raise SongUnavailable(response.status_code)
        if response.status_code != 200 or 'application/json' not in response.headers.get('Content-Type', ''):
            raise ValueError('pocklo share unavailable')
        data = bytearray()
        for chunk in response.iter_content(16384):
            data.extend(chunk)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError('pocklo share response too large')
    value = json.loads(data)
    if not isinstance(value, dict) or value.get('code') != 0 or not isinstance(value.get('body'), dict):
        raise ValueError('pocklo share unavailable')
    return value['body']


def song_metadata(token):
    data = public_body('/song-shares/' + token)
    if (data.get('schema') != 'pocklo.song.v1' or data.get('type') != 'song'
            or not isinstance(data.get('song_id'), str) or not re.fullmatch(TOKEN, data['song_id'])
            or type(data.get('version')) is not int or not isinstance(data.get('title'), str)
            or not isinstance(data.get('artists'), list)
            or not all(isinstance(artist, str) for artist in data['artists'])
            or type(data.get('available')) is not bool):
        raise ValueError('invalid song')
    # Explicit allowlist prevents future upstream signed fields entering persistent previews.
    result = {key: data.get(key) for key in (
        'schema', 'type', 'song_id', 'version', 'available', 'title', 'artists',
        'album', 'duration_ms', 'cover', 'lyrics',
    )} | {'share_url': f'https://{HOST}/share/{token}'}
    cover = data.get('cover')
    result['cover'] = {key: cover.get(key) for key in ('id', 'mime')} if isinstance(cover, dict) else None
    lyrics = data.get('lyrics') or {}
    result['lyrics'] = {
        'type': lyrics.get('type', 'none'),
        'text': lyrics.get('text', '') if isinstance(lyrics.get('text', ''), str) else '',
        'cues': [{'time_ms': cue['time_ms'], 'text': cue['text']}
                 for cue in lyrics.get('cues', []) if isinstance(cue, dict)
                 and type(cue.get('time_ms')) in (int, float) and cue['time_ms'] >= 0
                 and isinstance(cue.get('text'), str)],
    }
    return result


def song_playback(token):
    data = public_body('/song-shares/' + token + '/playback')
    if (data.get('schema') != 'pocklo.song.v1'
            or not isinstance(data.get('song_id'), str) or not re.fullmatch(TOKEN, data['song_id'])
            or type(data.get('version')) is not int or data['version'] < 1
            or not isinstance(data.get('expires_at'), str) or parse_datetime(data['expires_at']) is None):
        raise ValueError('invalid song playback')
    for key in ('audio', 'cover'):
        media = data.get(key)
        if media is None and key == 'cover':
            continue
        if not isinstance(media, dict) or not isinstance(media.get('url'), str):
            raise ValueError('invalid song media')
        if not isinstance(media.get('mime'), str) or not media['mime'].startswith('audio/' if key == 'audio' else 'image/'):
            raise ValueError('invalid song MIME')
        parsed = urlsplit(media['url'])
        local = settings.DEBUG and getattr(settings, 'POCKLO_TEST_API', None) and parsed.hostname in ('localhost', '127.0.0.1')
        if not local and (parsed.scheme != 'https' or parsed.netloc != 'res.6-79.cn' or parsed.fragment):
            raise ValueError('invalid song CDN')
    return {key: data.get(key) for key in ('schema', 'song_id', 'version', 'expires_at', 'expires_in', 'audio', 'cover')}


def metadata(token, share=None):
    if not re.fullmatch(TOKEN, token):
        raise ValueError('invalid pocklo share')
    item = (share if share is not None else public_body('/shares/' + token)).get('resource')
    if (not isinstance(item, dict) or not isinstance(item.get('name'), str)
            or not isinstance(item.get('id'), str) or not RESOURCE_ID.fullmatch(item['id'])
            or type(item.get('folder')) is not bool):
        raise ValueError('invalid pocklo resource')
    return item


def download_target(token):
    item = metadata(token)
    if item['folder'] or item.get('preview') == 'link':
        raise ValueError('pocklo share is not a file')
    data = public_body('/res/' + item['id'] + '/content', {'share': token, 'download': '1'})
    url = data.get('url', '')
    if data.get('kind') != 'download' or not isinstance(url, str):
        raise ValueError('pocklo download unavailable')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'res.6-79.cn' or parsed.fragment:
        raise ValueError('invalid pocklo download')
    return url


def media_thumbnail(item):
    if item['folder'] or item.get('preview') not in ('image', 'video'):
        return None
    url = item.get('thumbnail')
    if not isinstance(url, str) or any(char.isspace() for char in url):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme == 'https' and parsed.netloc == 'res.6-79.cn' and parsed.path and not parsed.fragment:
        return url


def thumbnail_target(token):
    url = media_thumbnail(metadata(token))
    if not url:
        raise ValueError('pocklo thumbnail unavailable')
    return url


def preview(token):
    share = public_body('/shares/' + token)
    if share.get('type') == 'song':
        song = song_metadata(token)
        reference = {key: song[key] for key in ('schema', 'song_id', 'version', 'title', 'artists', 'album', 'duration_ms', 'available')}
        reference.update(provider='pocklo_music', canonical_url=song['share_url'], audio_url='')
        return dict(url=song['share_url'], title=song['title'], description=' / '.join(song['artists']),
                    image_url='', site_name='小兜 Pocklo', favicon_url='', provider_data=reference)
    item = metadata(token, share)
    canonical = f'https://{HOST}/share/{token}'
    kind = 'folder' if item['folder'] else 'link' if item.get('preview') == 'link' else 'file'
    title = item['name'][:255] or 'Pocklo'
    preview_type = item.get('preview')
    return dict(url=canonical, title=title, description='', image_url='', site_name='Pocklo',
                favicon_url='', provider_data={'provider': 'pocklo_share', 'url': canonical,
                                               'title': title, 'kind': kind,
                                               'preview': preview_type if isinstance(preview_type, str) else '',
                                               'has_thumbnail': bool(media_thumbnail(item))})
