"""Best-effort durable public-note thumbnails in the existing private Qiniu bucket."""
import hashlib
import io
import warnings
import requests
from PIL import Image
from Message.providers.justone import JustOneProvider
from utils.qiniu import QINIU_UPLOAD_URL, build_upload_token


def cache_cover(url):
    if not JustOneProvider.media(url):
        return ''
    try:
        # No redirects to untrusted hosts; never fetch an arbitrary provider URL.
        with requests.get(url, stream=True, allow_redirects=False, timeout=(3, 8)) as response:
            response.raise_for_status()
            if response.status_code != 200:
                return ''
            data = bytearray()
            for chunk in response.iter_content(16384):
                data.extend(chunk)
                if len(data) > 3 * 1024 * 1024:
                    return ''
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 16_000_000:
                    return ''
                image.thumbnail((960, 960))
                out = io.BytesIO()
                image.convert('RGB').save(out, 'WEBP', quality=80)
        content = out.getvalue()
        key = 'frienden/social-covers/' + hashlib.sha256(content).hexdigest() + '.webp'
        result = requests.post(QINIU_UPLOAD_URL, data={'key': key, 'token': build_upload_token(key, max_file_size=3 * 1024 * 1024)},
                               files={'file': ('cover.webp', content, 'image/webp')}, timeout=(3, 15))
        result.raise_for_status()
        return key if result.json().get('key') == key else ''
    except Exception:
        # Thumbnail storage never triggers another paid parse or destroys metadata.
        return ''
