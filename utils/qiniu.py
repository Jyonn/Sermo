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
AVATAR_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5M
CHAT_BACKGROUND_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10M
AVATAR_DOWNLOAD_EXPIRE_SECONDS = 30 * 24 * 60 * 60
AVATAR_PREFIX = 'sermo/avatar/'
CHAT_BACKGROUND_PREFIX = 'sermo/chat-background/'
SPACE_IDENTITY_PREFIX = 'sermo/space-identity/'
SPACE_IDENTITY_MAX_FILE_SIZE = 10 * 1024 * 1024
MESSAGE_MEDIA_PREFIX = 'sermo/messages'
MESSAGE_MEDIA_MAX_FILE_SIZE = {
    'image': 10 * 1024 * 1024,
    'video': 500 * 1024 * 1024,
    'audio': 20 * 1024 * 1024,
    'file': 100 * 1024 * 1024,
    'sticker': 10 * 1024 * 1024,
}
MESSAGE_MEDIA_ALLOWED_EXTENSIONS = {
    'image': {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'},
    'video': {'.mp4', '.mov', '.m4v', '.webm', '.ogv'},
    'audio': {'.mp3', '.wav', '.m4a', '.ogg', '.aac', '.webm'},
    'file': None,
    'sticker': {'.jpg', '.jpeg', '.png', '.gif', '.webp'},
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
CHAT_BACKGROUND_SAFE_KEY_PATTERN = re.compile(
    r'^sermo/chat-background/[A-Za-z0-9][A-Za-z0-9._-]*$'
)


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


def sign_private_processed_url(url: str, fops: str, expire_seconds: int = AVATAR_DOWNLOAD_EXPIRE_SECONDS):
    normalized_url = (url or '').strip()
    normalized_fops = (fops or '').strip().lstrip('?')
    if not normalized_url or not normalized_fops:
        return ''
    separator = '&' if '?' in normalized_url else '?'
    return sign_private_download_url(f'{normalized_url}{separator}{normalized_fops}', expire_seconds=expire_seconds)


def build_message_image_thumbnail_uri(uri: str, width: int = 120):
    normalized_width = max(48, min(int(width), 480))
    return sign_private_processed_url(uri, f'imageView2/2/w/{normalized_width}/q/70')


def build_sticker_display_uri(uri: str, width: int = 320):
    normalized_width = max(120, min(int(width), 480))
    return sign_private_processed_url(uri, f'imageView2/2/w/{normalized_width}/q/75')


def build_message_video_thumbnail_uri(uri: str, width: int = 480):
    normalized_width = max(120, min(int(width), 960))
    return sign_private_processed_url(uri, f'vframe/jpg/offset/0.1/w/{normalized_width}')


def build_avatar_display_uri(uri: str, size: int = 400):
    normalized_size = max(96, min(int(size), 1024))
    return sign_private_processed_url(uri, f'imageView2/1/w/{normalized_size}/h/{normalized_size}/q/85')


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
    if kind not in MESSAGE_MEDIA_ALLOWED_EXTENSIONS:
        raise MessageErrors.MEDIA_KIND_INVALID

    allowed_extensions = MESSAGE_MEDIA_ALLOWED_EXTENSIONS[kind]
    if kind == 'file':
        extension = os.path.splitext(os.path.basename((file_name or '').strip()))[1].lower()
        if extension and len(extension) <= 32 and re.fullmatch(r'\.[a-z0-9][a-z0-9._+-]*', extension):
            return extension
        return '.bin'

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


def build_chat_background_key(file_name: str, content_type: str = None):
    extension = _guess_extension(file_name, content_type)
    return f'{CHAT_BACKGROUND_PREFIX}{uuid.uuid4().hex}{extension}'


def validate_chat_background_key(key: str):
    normalized = (key or '').strip()
    if not CHAT_BACKGROUND_SAFE_KEY_PATTERN.fullmatch(normalized):
        raise UserErrors.CHAT_BACKGROUND_KEY_INVALID
    extension = os.path.splitext(normalized)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise UserErrors.CHAT_BACKGROUND_FILE_TYPE_INVALID
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


def _delete_file(key: str):
    content_type = 'application/x-www-form-urlencoded'
    body = f'op=/delete/{_entry_uri(key)}'
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


def delete_file(key: str):
    return _delete_file(validate_avatar_key(key))


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


def key_from_chat_background_uri(uri: str):
    normalized = (uri or '').strip()
    if not normalized:
        return None
    parsed = urlparse(normalized)
    path = (parsed.path or '').lstrip('/')
    if not parsed.scheme or not parsed.netloc or not path.startswith(CHAT_BACKGROUND_PREFIX):
        return None
    return validate_chat_background_key(path)


def delete_chat_background_by_uri(uri: str):
    key = key_from_chat_background_uri(uri)
    if not key:
        return None
    return _delete_file(key)


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


def issue_chat_background_upload(file_name: str, content_type: str = None):
    key = build_chat_background_key(file_name=file_name, content_type=content_type)
    uri = avatar_uri_for_key(key)
    return dict(
        upload_token=build_upload_token(key, max_file_size=CHAT_BACKGROUND_MAX_FILE_SIZE),
        upload_url=QINIU_UPLOAD_URL,
        key=key,
        resource_uri=sign_private_download_url(uri),
        expires_in=QINIU_TOKEN_EXPIRE_SECONDS,
        max_file_size=CHAT_BACKGROUND_MAX_FILE_SIZE,
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

    key_name = normalized_key[len(prefix):]
    if not re.fullmatch(r'[a-f0-9]{32}(?:\.[A-Za-z0-9][A-Za-z0-9._+-]{0,31})?', key_name):
        raise MessageErrors.PAYLOAD_INVALID

    extension = os.path.splitext(key_name)[1].lower()
    allowed_extensions = MESSAGE_MEDIA_ALLOWED_EXTENSIONS[normalized_kind]
    if allowed_extensions is not None and extension not in allowed_extensions:
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


def issue_space_identity_upload(space_id, file_name, content_type=None):
    extension = os.path.splitext(str(file_name or '').strip())[1].lower()
    if extension != '.pdf' or str(content_type or '').strip().lower() not in {'application/pdf', 'application/x-pdf'}:
        from Space.validators import SpaceErrors
        raise SpaceErrors.IDENTITY_FILE_INVALID
    key = f'{SPACE_IDENTITY_PREFIX}{int(space_id)}/{uuid.uuid4().hex}.pdf'
    return dict(
        upload_token=build_upload_token(key, max_file_size=SPACE_IDENTITY_MAX_FILE_SIZE),
        upload_url=QINIU_UPLOAD_URL,
        key=key,
        expires_in=QINIU_TOKEN_EXPIRE_SECONDS,
        max_file_size=SPACE_IDENTITY_MAX_FILE_SIZE,
    )


def validate_space_identity_key(space_id, key):
    normalized = str(key or '').strip()
    prefix = f'{SPACE_IDENTITY_PREFIX}{int(space_id)}/'
    if not normalized.startswith(prefix) or not normalized.endswith('.pdf'):
        from Space.validators import SpaceErrors
        raise SpaceErrors.IDENTITY_FILE_INVALID
    return normalized


def issue_sticker_upload(content_hash: str, file_name: str, content_type: str = None):
    extension = _guess_extension_by_kind('sticker', file_name, content_type)
    key = f'{MESSAGE_MEDIA_PREFIX}/sticker/{content_hash}{extension}'
    return dict(
        kind='sticker',
        upload_token=build_upload_token(key, max_file_size=MESSAGE_MEDIA_MAX_FILE_SIZE['sticker']),
        upload_url=QINIU_UPLOAD_URL,
        key=key,
        resource_uri=sign_private_download_url(avatar_uri_for_key(key)),
        expires_in=QINIU_TOKEN_EXPIRE_SECONDS,
        max_file_size=MESSAGE_MEDIA_MAX_FILE_SIZE['sticker'],
    )


def delete_sticker_file(key: str):
    return _delete_file(validate_message_media_key('sticker', key))
