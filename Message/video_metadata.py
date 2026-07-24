import datetime
import re

import requests
from django.utils import timezone

from utils.qiniu import sign_private_processed_url


ISO6709_RE = re.compile(r'([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)')


def fetch_qiniu_avinfo(source_uri: str):
    response = requests.get(
        sign_private_processed_url(source_uri, 'avinfo', expire_seconds=300),
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError('invalid avinfo response')
    return payload


def _number(value, cast=float):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _tags(*sources):
    merged = {}
    for source in sources:
        if isinstance(source, dict):
            merged.update(source.get('tags') or {})
    return merged


def _tag(tags, *names, limit=255):
    lowered = {str(key).lower(): value for key, value in tags.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ''):
            return str(value).strip()[:limit]
    return ''


def _taken_at(tags):
    value = _tag(tags, 'creation_time', 'date', limit=64)
    if not value:
        return None
    normalized = value.replace('Z', '+00:00')
    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _coordinates(tags):
    value = _tag(
        tags,
        'com.apple.quicktime.location.ISO6709',
        'location',
        'location-eng',
        limit=128,
    )
    match = ISO6709_RE.search(value)
    if not match:
        return None, None
    latitude = _number(match.group(1))
    longitude = _number(match.group(2))
    if latitude is None or longitude is None:
        return None, None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None, None
    return round(latitude, 7), round(longitude, 7)


def _frame_rate(value):
    if not value:
        return None
    text = str(value)
    if '/' not in text:
        return _number(text)
    numerator, denominator = text.split('/', 1)
    denominator_value = _number(denominator)
    if not denominator_value:
        return None
    return _number(numerator) / denominator_value


def parse_avinfo(raw):
    streams = raw.get('streams') or []
    video = next((stream for stream in streams if stream.get('codec_type') == 'video'), {})
    audio = next((stream for stream in streams if stream.get('codec_type') == 'audio'), {})
    format_info = raw.get('format') or {}
    tags = _tags(format_info, video, audio)
    latitude, longitude = _coordinates(tags)
    width = _number(video.get('width'), int)
    height = _number(video.get('height'), int)
    if not width or not height:
        raise ValueError('incomplete avinfo video dimensions')
    return dict(
        duration_seconds=_number(format_info.get('duration') or video.get('duration')),
        file_size=_number(format_info.get('size'), int),
        pixel_width=width,
        pixel_height=height,
        frame_rate=_frame_rate(video.get('avg_frame_rate') or video.get('r_frame_rate')),
        bit_rate=_number(format_info.get('bit_rate') or video.get('bit_rate'), int),
        video_codec=str(video.get('codec_name') or '')[:64],
        audio_codec=str(audio.get('codec_name') or '')[:64],
        make=_tag(tags, 'com.apple.quicktime.make', 'make'),
        model=_tag(tags, 'com.apple.quicktime.model', 'model'),
        lens_model=_tag(tags, 'com.apple.quicktime.lens.model', 'lens_model'),
        software=_tag(tags, 'com.apple.quicktime.software', 'software', 'encoder'),
        taken_at=_taken_at(tags),
        latitude=latitude,
        longitude=longitude,
    )
