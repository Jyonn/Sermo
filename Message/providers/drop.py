"""Drop links retain only a stable reference in LinkPreview, never diary text."""
import json
import re
import time
import math
import uuid
from datetime import datetime
from urllib.parse import urlsplit, parse_qs

import requests


class DropProvider:
    @staticmethod
    def token(value):
        if not isinstance(value, str):
            return None
        try:
            url = urlsplit(value or '')
            query = parse_qs(url.query, keep_blank_values=True)
            tokens = query.get('share', [])
            if (url.scheme == 'https' and url.netloc == 'drop.jyonn.space' and url.path == '/'
                    and not url.fragment and set(query) == {'share'} and len(tokens) == 1
                    and re.fullmatch(r'[A-Za-z0-9_-]{43}', tokens[0])):
                return tokens[0]
        except (ValueError, TypeError):
            pass
        return None

    @classmethod
    def preview(cls, url):
        token = cls.token(url)
        if not token:
            return None
        canonical = f'https://drop.jyonn.space/?share={token}'
        return dict(url=canonical, title='一滴日记', description='', image_url='', site_name='一滴',
                    favicon_url='', provider_data={'provider': 'drop_share', 'canonical_url': canonical})

    @classmethod
    def read(cls, url, *, detail=False):
        token = cls.token(url)
        if not token:
            raise ValueError('invalid drop reference')
        started = time.monotonic()
        with requests.get(f'https://api.drop.jyonn.space/api/public/shares/{token}/',
                          headers={'Accept': 'application/json'}, timeout=(3, 7),
                          allow_redirects=False, stream=True) as response:
            if response.status_code in (404, 410):
                return {'status': 'gone'}
            if response.status_code != 200:
                raise ValueError('drop unavailable')
            body = bytearray()
            for chunk in response.iter_content(16384):
                body.extend(chunk)
                if len(body) > 1024 * 1024:
                    raise ValueError('drop response too large')
            data = json.loads(body)
        rows = data.get('entries') if isinstance(data, dict) else None
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError('invalid drop response')
        if not rows:
            return {'status': 'gone'}
        try:
            expires = datetime.fromisoformat(data['expires_at'])
            server = datetime.fromisoformat(data['server_time'])
            if expires.tzinfo is None or server.tzinfo is None:
                raise ValueError('invalid share clock')
            remaining = int((expires-server).total_seconds()-(time.monotonic()-started))
        except (KeyError, TypeError, ValueError):
            raise ValueError('missing share expiry') from None
        if remaining <= 0:
            return {'status': 'gone'}
        row = rows[0]
        if not isinstance(row, dict) or not isinstance(row.get('text', ''), str):
            raise ValueError('invalid drop entry')
        text = row.get('text', '').strip()
        occurred = row.get('occurred_at')
        result = {'status': 'ready', 'text': text[:240], 'truncated': len(text) > 240,
                'occurred_at': occurred[:50] if isinstance(occurred, str) else None,
                'count': len(rows), 'expires_at': expires.isoformat(), 'remaining_seconds': remaining}
        if detail:
            entries = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('text', ''), str):
                    raise ValueError('invalid drop entry')
                entry = {'text': row.get('text', '')}
                for key in ('occurred_at', 'city', 'place'):
                    if isinstance(row.get(key), str): entry[key] = row[key]
                for key, bound in (('latitude', 90), ('longitude', 180)):
                    value = row.get(key)
                    if type(value) in (int, float) and math.isfinite(value) and -bound <= value <= bound:
                        entry[key] = value
                entry['audio'] = []
                for audio in row.get('audio', []) if isinstance(row.get('audio'), list) else []:
                    if not isinstance(audio, dict): continue
                    try: identity = str(uuid.UUID(audio.get('id', '')))
                    except (ValueError, TypeError, AttributeError): continue
                    duration = audio.get('duration_seconds')
                    entry['audio'].append({'id': identity, 'duration_seconds': duration if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0 else None})
                entries.append(entry)
            result['entries'] = entries
        return result

    @classmethod
    def audio(cls, url, recording):
        token = cls.token(url)
        try: identity = str(uuid.UUID(recording))
        except (ValueError, TypeError, AttributeError): raise ValueError('invalid audio') from None
        if not token: raise ValueError('invalid drop reference')
        response = requests.get(f'https://api.drop.jyonn.space/api/public/shares/{token}/audio/{identity}/',
                                headers={'Accept':'application/json'},timeout=(3,7),allow_redirects=False)
        if response.status_code in (404,410): return {'status':'gone'}
        if response.status_code != 200: raise ValueError('audio unavailable')
        if len(response.content) > 32768: raise ValueError('invalid audio response')
        value = response.json()
        signed = value.get('url') if isinstance(value,dict) else None
        parsed = urlsplit(signed) if isinstance(signed,str) else None
        if not parsed or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('invalid audio address')
        lifetime = value.get('expires_in')
        if type(lifetime) is not int or not 0 < lifetime <= 60: raise ValueError('invalid audio lifetime')
        return {'status':'ready','url':signed,'expires_in':lifetime}
