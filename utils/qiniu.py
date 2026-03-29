import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import time
import uuid
from urllib.parse import urlparse

import requests
from Config.models import Config, CI
from Message.validators import MessageErrors
from User.validators import UserErrors


QINIU_UPLOAD_URL = 'https://upload.qiniup.com'
QINIU_RS_HOST = 'rs.qiniuapi.com'
QINIU_RS_BATCH_URL = f'https://{QINIU_RS_HOST}/batch'
QINIU_TOKEN_EXPIRE_SECONDS = 10 * 60
AVATAR_MAX_FILE_SIZE = 5 * 1024 * 1024
AVATAR_DOWNLOAD_EXPIRE_SECONDS = 30 * 24 * 60 * 60
AVATAR_PREFIX = 'sermo/avatar/'
MESSAGE_MEDIA_PREFIX = 'sermo/messages'
MESSAGE_MEDIA_MAX_FILE_SIZE = {
    'image': 10 * 1024 * 1024,
    'video': 500 * 1024 * 1024,
    'audio': 20 * 1024 * 1024,
}
MESSAGE_MEDIA_ALLOWED_EXTENSIONS = {
    'image': {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'},
    'video': {'.mp4', '.mov', '.m4v', '.webm', '.ogv'},
    'audio': {'.mp3', '.wav', '.m4a', '.ogg', '.aac', '.webm'},
}
ALLOWED_IMAGE_EXTENSIONS = {
    '.jpg',
    '.jpeg',
    '.png',
    '.gif',
    '.webp',
    '.bmp',
    '.svg',
}
SAFE_KEY_PATTERN = re.compile(r'^sermo/avatar/[A-Za-z0-9][A-Za-z0-9._-]*$')


def _urlsafe_base64(data: bytes):
    return base64.urlsafe_b64encode(data).decode()


def _required_config(key: str):
    value = Config.get_value_by_key(key, default=None)
    normalized = (str(value).strip() if value is not None else '')
    if not normalized:
        raise UserErrors.AVATAR_STORAGE_NOT_CONFIGURED
    return normalized


def _normalize_domain(domain: str):
    normalized = (domain or '').strip()
    normalized = normalized.replace('https://', '').replace('http://', '')
    return normalized.strip('/')


def avatar_base_url():
    domain = _normalize_domain(_required_config(CI.QINIU_DOMAIN))
    return f'https://{domain}'


def avatar_uri_for_key(key: str):
    return f'{avatar_base_url()}/{key}'


def sign_private_download_url(url: str, expire_seconds: int = AVATAR_DOWNLOAD_EXPIRE_SECONDS):
    normalized_url = (url or '').strip()
    if not normalized_url:
        return ''

    access_key = _required_config(CI.QINIU_ACCESS_KEY)
    secret_key = _required_config(CI.QINIU_SECRET_KEY)
    deadline = int(time.time()) + expire_seconds
    separator = '&' if '?' in normalized_url else '?'
    download_url = f'{normalized_url}{separator}e={deadline}'
    digest = hmac.new(secret_key.encode(), download_url.encode(), hashlib.sha1).digest()
    encoded_digest = _urlsafe_base64(digest)
    token = f'{access_key}:{encoded_digest}'
    return f'{download_url}&token={token}'


def _guess_extension(file_name: str, content_type: str = None):
    extension = os.path.splitext((file_name or '').strip())[1].lower()
    if extension in ALLOWED_IMAGE_EXTENSIONS:
        return extension

    guessed = mimetypes.guess_extension((content_type or '').strip().lower(), strict=False)
    if guessed == '.jpe':
        guessed = '.jpeg'
    if guessed in ALLOWED_IMAGE_EXTENSIONS:
        return guessed

    raise UserErrors.AVATAR_FILE_TYPE_INVALID


def _guess_extension_by_kind(kind: str, file_name: str, content_type: str = None):
    allowed_extensions = MESSAGE_MEDIA_ALLOWED_EXTENSIONS.get(kind)
    if not allowed_extensions:
        raise MessageErrors.MEDIA_KIND_INVALID

    extension = os.path.splitext((file_name or '').strip())[1].lower()
    if extension in allowed_extensions:
        return extension

    guessed = mimetypes.guess_extension((content_type or '').strip().lower(), strict=False)
    if guessed == '.jpe':
        guessed = '.jpeg'
    if guessed in allowed_extensions:
        return guessed

    raise MessageErrors.PAYLOAD_INVALID


def build_avatar_key(file_name: str, content_type: str = None):
    extension = _guess_extension(file_name, content_type)
    return f'{AVATAR_PREFIX}{uuid.uuid4().hex}{extension}'


def validate_avatar_key(key: str):
    normalized = (key or '').strip()
    if not SAFE_KEY_PATTERN.fullmatch(normalized):
        raise UserErrors.AVATAR_KEY_INVALID
    extension = os.path.splitext(normalized)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise UserErrors.AVATAR_FILE_TYPE_INVALID
    return normalized


def build_upload_token(key: str, expire_seconds: int = QINIU_TOKEN_EXPIRE_SECONDS, max_file_size: int = None):
    access_key = _required_config(CI.QINIU_ACCESS_KEY)
    secret_key = _required_config(CI.QINIU_SECRET_KEY)
    bucket = _required_config(CI.QINIU_BUCKET)

    policy = dict(
        scope=f'{bucket}:{key}',
        deadline=int(time.time()) + expire_seconds,
    )
    if max_file_size is not None:
        policy['fsizeLimit'] = int(max_file_size)
    encoded_policy = _urlsafe_base64(json.dumps(policy, separators=(',', ':')).encode())
    digest = hmac.new(secret_key.encode(), encoded_policy.encode(), hashlib.sha1).digest()
    encoded_digest = _urlsafe_base64(digest)
    return f'{access_key}:{encoded_digest}:{encoded_policy}'


def _management_token(path: str, body: str, content_type: str):
    access_key = _required_config(CI.QINIU_ACCESS_KEY)
    secret_key = _required_config(CI.QINIU_SECRET_KEY)
    signing_str = f'POST {path}\nHost: {QINIU_RS_HOST}\nContent-Type: {content_type}\n\n{body}'
    digest = hmac.new(secret_key.encode(), signing_str.encode(), hashlib.sha1).digest()
    encoded_digest = _urlsafe_base64(digest)
    return f'Qiniu {access_key}:{encoded_digest}'


def _entry_uri(key: str):
    bucket = _required_config(CI.QINIU_BUCKET)
    return _urlsafe_base64(f'{bucket}:{key}'.encode())


def delete_file(key: str):
    normalized_key = validate_avatar_key(key)
    content_type = 'application/x-www-form-urlencoded'
    body = f'op=/delete/{_entry_uri(normalized_key)}'
    response = requests.post(
        QINIU_RS_BATCH_URL,
        data=body,
        headers={
            'Content-Type': content_type,
            'Authorization': _management_token('/batch', body, content_type),
        },
        timeout=10,
    )
    if response.status_code != 200:
        raise UserErrors.AVATAR_DELETE_FAILED(details=response.text)

    try:
        payload = response.json()
    except ValueError as err:
        raise UserErrors.AVATAR_DELETE_FAILED(details=err)

    if not isinstance(payload, list) or not payload:
        raise UserErrors.AVATAR_DELETE_FAILED(details=payload)

    item = payload[0] or {}
    code = item.get('code')
    if code in (200, 612):
        return item
    raise UserErrors.AVATAR_DELETE_FAILED(details=item)


def key_from_avatar_uri(avatar_uri: str):
    normalized = (avatar_uri or '').strip()
    if not normalized:
        return None

    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        return None

    path = (parsed.path or '').lstrip('/')
    if not path.startswith(AVATAR_PREFIX):
        return None
    return validate_avatar_key(path)


def delete_avatar_by_uri(avatar_uri: str):
    key = key_from_avatar_uri(avatar_uri)
    if not key:
        return None
    return delete_file(key)


def issue_avatar_upload(file_name: str, content_type: str = None):
    key = build_avatar_key(file_name=file_name, content_type=content_type)
    avatar_uri = avatar_uri_for_key(key)
    return dict(
        upload_token=build_upload_token(key, max_file_size=AVATAR_MAX_FILE_SIZE),
        upload_url=QINIU_UPLOAD_URL,
        key=key,
        avatar_uri=sign_private_download_url(avatar_uri),
        expires_in=QINIU_TOKEN_EXPIRE_SECONDS,
        max_file_size=AVATAR_MAX_FILE_SIZE,
    )


def build_message_media_key(kind: str, file_name: str, content_type: str = None):
    extension = _guess_extension_by_kind(kind, file_name, content_type)
    return f'{MESSAGE_MEDIA_PREFIX}/{kind}/{uuid.uuid4().hex}{extension}'


def validate_message_media_key(kind: str, key: str):
    normalized_kind = (kind or '').strip().lower()
    if normalized_kind not in MESSAGE_MEDIA_ALLOWED_EXTENSIONS:
        raise MessageErrors.MEDIA_KIND_INVALID

    normalized_key = (key or '').strip()
    prefix = f'{MESSAGE_MEDIA_PREFIX}/{normalized_kind}/'
    if not normalized_key.startswith(prefix):
        raise MessageErrors.PAYLOAD_INVALID

    extension = os.path.splitext(normalized_key)[1].lower()
    if extension not in MESSAGE_MEDIA_ALLOWED_EXTENSIONS[normalized_kind]:
        raise MessageErrors.PAYLOAD_INVALID
    return normalized_key


def issue_message_upload(kind: str, file_name: str, content_type: str = None):
    normalized_kind = (kind or '').strip().lower()
    if normalized_kind not in MESSAGE_MEDIA_MAX_FILE_SIZE:
        raise MessageErrors.MEDIA_KIND_INVALID

    key = build_message_media_key(normalized_kind, file_name=file_name, content_type=content_type)
    resource_uri = avatar_uri_for_key(key)
    return dict(
        kind=normalized_kind,
        upload_token=build_upload_token(
            key,
            max_file_size=MESSAGE_MEDIA_MAX_FILE_SIZE[normalized_kind],
        ),
        upload_url=QINIU_UPLOAD_URL,
        key=key,
        resource_uri=sign_private_download_url(resource_uri),
        expires_in=QINIU_TOKEN_EXPIRE_SECONDS,
        max_file_size=MESSAGE_MEDIA_MAX_FILE_SIZE[normalized_kind],
    )
