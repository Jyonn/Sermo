"""Public decorative metadata; never expose the rest of a campaign's config."""
from urllib.parse import urlsplit


def presentation(config):
    source = config.get('presentation')
    if not isinstance(source, dict):
        return None
    result = {}
    if source.get('artwork_layout') in ('image', 'keychain-sheet'):
        result['artwork_layout'] = source['artwork_layout']
    if source.get('palette') in ('sage', 'rose', 'ocean', 'gold'):
        result['palette'] = source['palette']
    for key in ('artwork_url', 'title_art_url'):
        value = source.get(key)
        if not isinstance(value, str) or len(value) > 1000 or '\\' in value or any(ord(c) < 32 for c in value):
            continue
        try:
            url = urlsplit(value)
            if (value.startswith('/') and not value.startswith('//')) or (url.scheme == 'https' and url.hostname and not url.username and not url.password):
                result[key] = value
        except ValueError:
            continue
    return result or None
