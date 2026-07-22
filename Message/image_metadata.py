import datetime
import re
import threading
import time

import requests
from django.utils import timezone

from utils.global_settings import Globals
from utils.qiniu import sign_private_processed_url


_geocode_lock = threading.Lock()
_last_geocode_at = 0.0


def _value(raw, key):
    value = raw.get(key)
    if isinstance(value, dict):
        value = value.get('val', value.get('value'))
    return value


def _text(raw, *keys, limit=255):
    for key in keys:
        value = _value(raw, key)
        if value not in (None, ''):
            return str(value).strip()[:limit]
    return ''


def _number(value):
    text = str(value or '').strip()
    if not text:
        raise ValueError('empty coordinate')
    if '/' in text:
        numerator, denominator = text.split('/', 1)
        return float(numerator) / float(denominator)
    return float(text)


def _coordinate(value, reference):
    parts = [part for part in re.split(r'[,\s]+', str(value or '').strip()) if part]
    if not parts:
        return None
    numbers = [_number(part) for part in parts[:3]]
    result = numbers[0]
    if len(numbers) > 1:
        result += numbers[1] / 60
    if len(numbers) > 2:
        result += numbers[2] / 3600
    if str(reference or '').upper() in ('S', 'W'):
        result *= -1
    return round(result, 7)


def _taken_at(raw):
    value = _text(raw, 'DateTimeOriginal', 'DateTimeDigitized', 'DateTime', limit=64)
    if not value:
        return None
    for pattern in ('%Y:%m:%d %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            parsed = datetime.datetime.strptime(value[:19], pattern)
            return timezone.make_aware(parsed, timezone.get_current_timezone())
        except ValueError:
            continue
    return None


def fetch_qiniu_exif(source_uri: str):
    response = requests.get(sign_private_processed_url(source_uri, 'exif', expire_seconds=300), timeout=12)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError('invalid EXIF response')
    return payload


def parse_exif(raw):
    latitude = _coordinate(_value(raw, 'GPSLatitude'), _value(raw, 'GPSLatitudeRef'))
    longitude = _coordinate(_value(raw, 'GPSLongitude'), _value(raw, 'GPSLongitudeRef'))
    return dict(
        make=_text(raw, 'Make'),
        model=_text(raw, 'Model'),
        lens_model=_text(raw, 'LensModel', 'LensInfo'),
        software=_text(raw, 'Software'),
        taken_at=_taken_at(raw),
        latitude=latitude,
        longitude=longitude,
    )


def reverse_geocode(latitude: float, longitude: float):
    global _last_geocode_at
    with _geocode_lock:
        wait_seconds = 1.05 - (time.monotonic() - _last_geocode_at)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        response = requests.get(
            Globals.REVERSE_GEOCODING_URL,
            params={
                'format': 'jsonv2',
                'lat': latitude,
                'lon': longitude,
                'zoom': 16,
                'addressdetails': 1,
                'accept-language': 'zh-CN,zh,en',
            },
            headers={'User-Agent': Globals.REVERSE_GEOCODING_USER_AGENT},
            timeout=12,
        )
        _last_geocode_at = time.monotonic()
    response.raise_for_status()
    payload = response.json()
    return str(payload.get('display_name') or '').strip()[:500]
