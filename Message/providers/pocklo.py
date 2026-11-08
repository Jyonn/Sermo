"""Read public Pocklo shares without retaining signed download URLs."""
import json
import re
from urllib.parse import urlsplit

import requests


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


def public_body(path, params=None):
    with requests.get(API + path, params=params, timeout=(3, 6),
                      stream=True, allow_redirects=False) as response:
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


def metadata(token):
    if not re.fullmatch(TOKEN, token):
        raise ValueError('invalid pocklo share')
    item = public_body('/shares/' + token).get('resource')
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


def preview(token):
    item = metadata(token)
    canonical = f'https://{HOST}/share/{token}'
    kind = 'folder' if item['folder'] else 'link' if item.get('preview') == 'link' else 'file'
    title = item['name'][:255] or 'Pocklo'
    return dict(url=canonical, title=title, description='', image_url='', site_name='Pocklo',
                favicon_url='', provider_data={'provider': 'pocklo_share', 'url': canonical,
                                               'title': title, 'kind': kind})
