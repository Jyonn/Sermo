"""Profile-scoped avatar presentation, never a general upload proxy."""
import secrets
from urllib.parse import quote, urlsplit

import requests
from django.core import signing
from django.http import HttpResponse
from django.views import View
from smartdjango import Error

from User.models import User, UserAvatarTypeChoice
from utils.qiniu import (
    AVATAR_MAX_FILE_SIZE, avatar_uri_for_key, key_from_avatar_uri,
    build_avatar_display_uri,
)

AVATAR_SALT = 'frienden.oauth.avatar.v1'
API_ORIGIN = 'https://api.sermo.jyonn.space'
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def custom_avatar_download(user):
    uri = (user.avatar_uri or '').strip()
    key = key_from_avatar_uri(uri)
    if not key:
        return None
    canonical = avatar_uri_for_key(key)
    source = urlsplit(uri)
    if source.scheme != 'https' or source.netloc != urlsplit(canonical).netloc or source.username or source.password:
        return None
    # Reuse the normal avatar crop, with raster output even for SVG uploads.
    return build_avatar_display_uri(canonical, raster=True, expire_seconds=60)


def profile_avatar_url(user):
    uri = (user.avatar_uri or '').strip()
    if not uri:
        return None
    try:
        if user.avatar_type == UserAvatarTypeChoice.PRESET:
            prefix = f'{User.AVATAR_PRESET_BASE_URI}/'
            if not uri.startswith(prefix) or not uri.endswith('.png'):
                return None
            preset = uri[len(prefix):-4]
            if not preset.isdigit():
                return None
            return User.build_preset_avatar_uri(int(preset))
        if user.avatar_type == UserAvatarTypeChoice.CUSTOM and custom_avatar_download(user):
            token = signing.Signer(salt=AVATAR_SALT).sign_object({
                'u': user.pk, 'v': user._dictify_avatar_cache_key(),
            })
            return f'{API_ORIGIN}/users/oauth/avatar/{quote(token, safe="")}'
    except (ValueError, TypeError, Error):
        return None
    return None


def image_response(content=b'', status=404):
    response = HttpResponse(content, status=status, content_type='image/png' if status == 200 else 'text/plain')
    response['Cache-Control'] = 'public, max-age=300' if status == 200 else 'no-store'
    response['Cross-Origin-Resource-Policy'] = 'cross-origin'
    response['Access-Control-Allow-Origin'] = '*'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


class OAuthAvatarView(View):
    def get(self, request, token):
        if len(token) > 512:
            return image_response()
        try:
            identity = signing.Signer(salt=AVATAR_SALT).unsign_object(token)
            user = User.objects.filter(pk=identity['u'], is_deleted=False, avatar_type=UserAvatarTypeChoice.CUSTOM).first()
            if not user or not secrets.compare_digest(user._dictify_avatar_cache_key(), identity['v']):
                return image_response()
            url = custom_avatar_download(user)
            if not url:
                return image_response()
            with requests.get(url, timeout=(5, 10), stream=True, allow_redirects=False) as upstream:
                if upstream.status_code != 200:
                    return image_response()
                data = bytearray()
                for chunk in upstream.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > AVATAR_MAX_FILE_SIZE:
                        return image_response()
            if not data.startswith(PNG_SIGNATURE):
                return image_response()
            return image_response(bytes(data), 200)
        except (signing.BadSignature, ValueError, KeyError, TypeError, Error, requests.RequestException):
            return image_response()
