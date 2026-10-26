import re
from urllib.parse import urlparse


SITE_HOSTS = frozenset({
    'sermo.jyonn.space', 'frienden.jyonn.space',
    'sermo.6-79.cn', 'frienden.6-79.cn',
    'localhost', '127.0.0.1',
})


def space_site_link(url, space_slug):
    """Accept exact first-party hosts and paths belonging to the sender's space."""
    try:
        parsed = urlparse(url)
        if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in SITE_HOSTS
                or parsed.username or parsed.password):
            return None
        if parsed.hostname not in {'localhost', '127.0.0.1'} and parsed.port not in {None, 80, 443}:
            return None
    except ValueError:
        return None
    match = re.fullmatch(r'/(?:([^/]+)/)?(app/.*)', parsed.path)
    if not match or (match[1] or space_slug).lower() != space_slug.lower():
        return None
    return parsed, '/' + match[2]
