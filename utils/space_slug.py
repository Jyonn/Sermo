SPACE_RESERVED_SLUGS = frozenset({
    # Frontend entry points. A space slug occupies the same first path segment.
    'entry', 'space', 'app', 'friend-invite', 'official-login', 'account-switch', 'pwa',
    # Static and infrastructure namespaces.
    'api', 'assets', 'icons', 'labs', 'static', 'cdn', 'www',
    'admin', 'mail', 'smtp', 'imap', 'pop', 'ftp', 'docs', 'status', 'support',
    'help', 'blog', 'dev', 'test', 'staging',
})


def is_reserved_space_slug(value):
    return str(value or '').strip().lower() in SPACE_RESERVED_SLUGS
