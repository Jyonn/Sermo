"""Public calendar bindings never store or publish Chronelle access keys."""
from urllib.parse import urlsplit
from uuid import UUID


def calendar_id_from_url(value):
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError('Invalid Chronelle share link')
    parsed = urlsplit(value.strip())
    if (parsed.scheme != 'https' or parsed.netloc != 'chronelle.jyonn.space'
            or parsed.path not in ('', '/', '/embed/') or parsed.query):
        raise ValueError('Invalid Chronelle share link')
    # Reject all parameters, including private keys, rather than silently publishing them.
    prefix = '/share/'
    if not parsed.fragment.startswith(prefix):
        raise ValueError('Invalid Chronelle share link')
    identifier = parsed.fragment[len(prefix):]
    if str(UUID(identifier)) != identifier.lower():
        raise ValueError('Invalid Chronelle calendar ID')
    return identifier.lower()
