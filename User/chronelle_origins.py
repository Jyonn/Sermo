"""Fixed provider and callback origins for the account-binding OAuth client."""

BASE = 'https://chronelle.jyonn.space'
MIRROR_BASE = 'https://chronelle.6-79.cn'
APP_ORIGINS = {'https://sermo.jyonn.space', 'https://frienden.jyonn.space',
               'https://sermo.6-79.cn', 'https://frienden.6-79.cn'}


def authorization_site(request):
    origin = request.headers.get('Origin', '')
    app_origin = origin if origin in APP_ORIGINS else 'https://sermo.jyonn.space'
    return (MIRROR_BASE if app_origin in {'https://sermo.6-79.cn', 'https://frienden.6-79.cn'} else BASE), app_origin
