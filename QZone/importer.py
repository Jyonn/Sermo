import datetime
import hashlib
import json
import mimetypes
import re
from pathlib import Path

import ijson
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from qiniu import Auth, put_file

from Config.models import CI, Config
from Message.models import MediaAsset
from QZone.models import QZoneComment, QZoneEmoticon, QZoneMedia, QZonePost, QZoneUser
from Space.models import Space
from Square.models import (
    Statement,
    StatementComment,
    StatementCommentMedia,
    StatementMedia,
    StatementVisibilityChoice,
)
from User.models import QQIdentity
from User.qq_identity import ensure_qzone_placeholder
from utils.qiniu import avatar_uri_for_key


STRUCTURED_MENTION_RE = re.compile(r'@\{uin:(?P<qq>\d+),nick:(?P<nick>.*?)(?:,who:.*)?\}')
QZONE_EMOTICON_RE = re.compile(r'\[em\](?P<code>e\d+)\[/em\]', re.IGNORECASE)
QZONE_EMOTICON_EXTENSIONS = {'.gif', '.png', '.jpg', '.jpeg', '.webp', '.bmp'}
EXPORT_TABLE_KEYS = ('qzone_user', 'qzone_post', 'qzone_comment')


def _chunks(rows, size):
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def _top_level_value_event(path, table_key):
    try:
        with path.open('rb') as source:
            waiting_for_value = False
            for prefix, event, value in ijson.parse(source, use_float=True):
                if prefix == '' and event == 'map_key':
                    waiting_for_value = value == table_key
                    continue
                if waiting_for_value:
                    return event
            return None
    except (OSError, UnicodeError, ijson.JSONError) as error:
        raise CommandError(f'Cannot parse migration file: {error}') from error


def iter_qzone_export(path):
    path = Path(path)
    if not path.is_file():
        raise CommandError(f'Input file does not exist: {path}')
    try:
        for table_key in EXPORT_TABLE_KEYS:
            found_row = False
            with path.open('rb') as source:
                for row in ijson.items(source, f'{table_key}.item', use_float=True):
                    found_row = True
                    if not isinstance(row, dict):
                        raise CommandError(f'{table_key} must contain JSON objects.')
                    yield table_key, row
            if not found_row:
                value_event = _top_level_value_event(path, table_key)
                if value_event is None:
                    raise CommandError(f'Migration file is missing list {table_key}.')
                if value_event != 'start_array':
                    raise CommandError(f'{table_key} must be a list.')
    except (OSError, UnicodeError, ijson.JSONError) as error:
        raise CommandError(f'Cannot parse migration file: {error}') from error


def _file_sha256(path):
    digest = hashlib.sha256()
    try:
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
    except OSError as error:
        raise CommandError(f'Cannot read migration file: {error}') from error
    return digest.hexdigest()


def _json_value(value, expected_type, field_name):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise CommandError(f'{field_name} is not valid JSON: {error}') from error
    if not isinstance(value, expected_type):
        raise CommandError(f'{field_name} must be {expected_type.__name__}')
    return value


def _datetime_value(value):
    parsed = parse_datetime(str(value or ''))
    if parsed is None:
        try:
            parsed = datetime.datetime.fromisoformat(str(value or ''))
        except ValueError as error:
            raise CommandError(f'Invalid datetime: {value}') from error
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def normalize_qzone_text(value):
    text = str(value or '').strip()
    return STRUCTURED_MENTION_RE.sub(lambda match: f'@{match.group("nick")}', text)


def extract_qzone_emoticon_codes(value):
    return tuple(dict.fromkeys(
        match.group('code').lower()
        for match in QZONE_EMOTICON_RE.finditer(str(value or ''))
    ))


class QZoneImporter:
    def __init__(self, space, input_path, stdout=None, batch_size=500):
        self.space = space
        self.input_path = Path(input_path).expanduser().resolve()
        self.data_root = self.input_path.parent
        self.stdout = stdout
        self.batch_size = max(1, int(batch_size))
        self._emoticon_files = None
        self._emoticons_by_code = None

    def write(self, message):
        if self.stdout is not None:
            self.stdout.write(str(message))

    def preflight(self):
        counts = {key: 0 for key in EXPORT_TABLE_KEYS}
        user_set = set()
        post_set = set()
        comment_set = set()
        unresolved_parents = set()
        missing_users = set()
        missing_posts = set()
        late_parents = set()
        private_posts = post_text_over_140 = comment_text_over_140 = 0

        for table_key, row in iter_qzone_export(self.input_path):
            counts[table_key] += 1
            if table_key == 'qzone_user':
                qq = str(row.get('qq') or '').strip()
                if qq in user_set:
                    raise CommandError('qzone_user contains duplicate QQ numbers.')
                user_set.add(qq)
                continue
            if table_key == 'qzone_post':
                post_id = int(row['id'])
                if post_id in post_set:
                    raise CommandError('qzone_post contains duplicate IDs.')
                post_set.add(post_id)
                author_qq = str(row.get('author_qq') or '').strip()
                if author_qq and author_qq not in user_set:
                    missing_users.add(author_qq)
                private_posts += row.get('visibility') == 'private'
                post_text_over_140 += len(str(row.get('content_text') or '')) > 140
                continue

            comment_id = int(row['id'])
            if comment_id in comment_set:
                raise CommandError('qzone_comment contains duplicate IDs.')
            post_id = int(row['post_id'])
            if post_id not in post_set:
                missing_posts.add(post_id)
            for field in ('author_qq', 'reply_to_qq'):
                qq = str(row.get(field) or '').strip()
                if qq and qq not in user_set:
                    missing_users.add(qq)
            if row.get('parent_comment_id'):
                parent_id = int(row['parent_comment_id'])
                if parent_id not in comment_set:
                    unresolved_parents.add(parent_id)
                if parent_id >= comment_id:
                    late_parents.add(parent_id)
            comment_set.add(comment_id)
            comment_text_over_140 += len(str(row.get('content_raw') or '')) > 140

        missing_parents = unresolved_parents - comment_set
        late_parents.update(unresolved_parents & comment_set)
        if missing_users or missing_posts or missing_parents or late_parents:
            raise CommandError(
                f'Dangling references: users={len(missing_users)}, posts={len(missing_posts)}, '
                f'parents={len(missing_parents)}, parents_not_before_children={len(late_parents)}'
            )
        report = {
            'file_sha256': _file_sha256(self.input_path),
            'users': counts['qzone_user'],
            'posts': counts['qzone_post'],
            'comments': counts['qzone_comment'],
            'private_posts': private_posts,
            'post_text_over_140': post_text_over_140,
            'comment_text_over_140': comment_text_over_140,
        }
        self.write(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    def import_source(self, limit=0):
        if limit:
            users_by_qq = {}
            posts = []
            comments = []
            post_ids = set()
            required_qqs = set()
            for table_key, row in iter_qzone_export(self.input_path):
                if table_key == 'qzone_user':
                    users_by_qq[str(row.get('qq') or '').strip()] = row
                elif table_key == 'qzone_post' and len(posts) < limit:
                    posts.append(row)
                    post_ids.add(int(row['id']))
                    required_qqs.add(str(row.get('author_qq') or '').strip())
                elif table_key == 'qzone_comment' and int(row['post_id']) in post_ids:
                    comments.append(row)
                    required_qqs.add(str(row.get('author_qq') or '').strip())
                    if row.get('reply_to_qq'):
                        required_qqs.add(str(row['reply_to_qq']).strip())
            users = [users_by_qq[qq] for qq in required_qqs if qq in users_by_qq]
            self._import_users(users)
            self._import_posts(posts)
            self._import_comments(comments)
            result = {'users': len(users), 'posts': len(posts), 'comments': len(comments)}
        else:
            handlers = {
                'qzone_user': self._import_users,
                'qzone_post': self._import_posts,
                'qzone_comment': self._import_comments,
            }
            counts = {key: 0 for key in EXPORT_TABLE_KEYS}
            active_key = None
            batch = []
            for table_key, row in iter_qzone_export(self.input_path):
                if active_key is not None and table_key != active_key and batch:
                    handlers[active_key](batch)
                    batch = []
                active_key = table_key
                batch.append(row)
                counts[table_key] += 1
                if len(batch) >= self.batch_size:
                    handlers[table_key](batch)
                    batch = []
            if active_key is not None and batch:
                handlers[active_key](batch)
            result = {
                'users': counts['qzone_user'],
                'posts': counts['qzone_post'],
                'comments': counts['qzone_comment'],
            }
        self.write(f'source: {result}')
        return result

    def _import_users(self, rows):
        for batch in _chunks(rows, self.batch_size):
            qqs = [str(row['qq']).strip() for row in batch]
            existing = QZoneUser.objects.in_bulk(qqs)
            creates = []
            updates = []
            for row in batch:
                qq = str(row['qq']).strip()
                nickname = str(row.get('nickname') or '')[:255]
                item = existing.get(qq)
                if item is None:
                    creates.append(QZoneUser(qq=qq, nickname=nickname))
                elif item.nickname != nickname:
                    item.nickname = nickname
                    updates.append(item)
            QZoneUser.objects.bulk_create(creates, batch_size=self.batch_size)
            if updates:
                QZoneUser.objects.bulk_update(updates, ['nickname'], batch_size=self.batch_size)

    def _import_posts(self, rows):
        fields = [
            'source_post_id', 'author', 'content_raw', 'content_text', 'published_at',
            'visibility', 'media', 'source_payload', 'created_at', 'updated_at',
        ]
        for batch in _chunks(rows, self.batch_size):
            ids = [int(row['id']) for row in batch]
            existing = QZonePost.objects.in_bulk(ids)
            creates = []
            updates = []
            for row in batch:
                values = self._post_values(row)
                item = existing.get(int(row['id']))
                if item is None:
                    creates.append(QZonePost(id=int(row['id']), **values))
                    continue
                if item.source_post_id != values['source_post_id'] or item.author_id != values['author_id']:
                    raise CommandError(f'QZone post ID {item.id} conflicts with existing source identity.')
                if not any(getattr(item, field) != value for field, value in values.items()):
                    continue
                for field, value in values.items():
                    setattr(item, field, value)
                updates.append(item)
            QZonePost.objects.bulk_create(creates, batch_size=self.batch_size)
            if updates:
                QZonePost.objects.bulk_update(updates, fields, batch_size=self.batch_size)

    @staticmethod
    def _post_values(row):
        return {
            'source_post_id': str(row.get('source_post_id') or '')[:64],
            'author_id': str(row.get('author_qq') or '').strip(),
            'content_raw': str(row.get('content_raw') or ''),
            'content_text': str(row.get('content_text') or ''),
            'published_at': _datetime_value(row.get('published_at')),
            'visibility': str(row.get('visibility') or 'public')[:16],
            'media': _json_value(row.get('media') or [], list, 'qzone_post.media'),
            'source_payload': _json_value(row.get('source_payload') or {}, dict, 'qzone_post.source_payload'),
            'created_at': _datetime_value(row.get('created_at') or row.get('published_at')),
            'updated_at': _datetime_value(row.get('updated_at') or row.get('created_at') or row.get('published_at')),
        }

    def _import_comments(self, rows):
        fields = [
            'post', 'author', 'reply_to', 'content_raw', 'published_at',
            'source_payload', 'created_at', 'updated_at',
        ]
        for batch in _chunks(rows, self.batch_size):
            ids = [int(row['id']) for row in batch]
            existing = QZoneComment.objects.in_bulk(ids)
            creates = []
            updates = []
            parent_updates = []
            for row in batch:
                values = self._comment_values(row)
                item = existing.get(int(row['id']))
                if item is None:
                    creates.append(QZoneComment(id=int(row['id']), parent_id=None, **values))
                    if row.get('parent_comment_id'):
                        parent_updates.append(QZoneComment(
                            id=int(row['id']),
                            parent_id=int(row['parent_comment_id']),
                        ))
                    continue
                if any(getattr(item, field) != value for field, value in values.items()):
                    for field, value in values.items():
                        setattr(item, field, value)
                    updates.append(item)
                parent_id = int(row['parent_comment_id']) if row.get('parent_comment_id') else None
                if item.parent_id != parent_id:
                    item.parent_id = parent_id
                    parent_updates.append(item)
            QZoneComment.objects.bulk_create(creates, batch_size=self.batch_size)
            if updates:
                QZoneComment.objects.bulk_update(updates, fields, batch_size=self.batch_size)
            if parent_updates:
                QZoneComment.objects.bulk_update(parent_updates, ['parent'], batch_size=self.batch_size)

    @staticmethod
    def _comment_values(row):
        reply_to = str(row.get('reply_to_qq') or '').strip() or None
        return {
            'post_id': int(row['post_id']),
            'author_id': str(row.get('author_qq') or '').strip(),
            'reply_to_id': reply_to,
            'content_raw': str(row.get('content_raw') or ''),
            'published_at': _datetime_value(row.get('published_at')),
            'source_payload': _json_value(row.get('source_payload') or {}, dict, 'qzone_comment.source_payload'),
            'created_at': _datetime_value(row.get('created_at') or row.get('published_at')),
            'updated_at': _datetime_value(row.get('updated_at') or row.get('created_at') or row.get('published_at')),
        }

    def import_identities(self, limit=0):
        self.space.require_qq_binding_granted()
        authored_qqs = set(QZonePost.objects.values_list('author_id', flat=True))
        authored_qqs.update(QZoneComment.objects.values_list('author_id', flat=True))
        authored_qqs.update(
            qq for qq in QZoneComment.objects.exclude(reply_to_id=None).values_list('reply_to_id', flat=True)
        )
        users = QZoneUser.objects.filter(qq__in=authored_qqs).order_by('qq')
        if limit:
            users = users[:limit]
        completed = 0
        for source_user in users.iterator(chunk_size=self.batch_size):
            ensure_qzone_placeholder(self.space, source_user.qq, source_user.nickname)
            completed += 1
        self.write(f'identities: {completed}')
        return completed

    @staticmethod
    def _source_root(payload):
        source_file = str((payload or {}).get('source_file') or '')
        marker = '/Messages/'
        return source_file.split(marker, 1)[0] if marker in source_file else ''

    def _emoticon_file_index(self):
        if self._emoticon_files is not None:
            return self._emoticon_files
        index = {}
        for directory in sorted(self.data_root.glob('*/Common/images')):
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                code = path.stem.lower()
                if (
                    path.is_file()
                    and path.suffix.lower() in QZONE_EMOTICON_EXTENSIONS
                    and re.fullmatch(r'e\d+', code)
                ):
                    index.setdefault(code, []).append(path)
        self._emoticon_files = index
        return index

    def _emoticon_source_path(self, code, root):
        candidates = self._emoticon_file_index().get(code, [])
        preferred_root = self.data_root / root if root else None
        if preferred_root is not None:
            for path in candidates:
                if path.is_relative_to(preferred_root):
                    return path.relative_to(self.data_root).as_posix()
        return candidates[0].relative_to(self.data_root).as_posix() if candidates else ''

    def _upsert_emoticon(self, code, root):
        if self._emoticons_by_code is None:
            self._emoticons_by_code = QZoneEmoticon.objects.in_bulk()
        emoticon = self._emoticons_by_code.get(code)
        existing_path = self.data_root / emoticon.source_path if emoticon and emoticon.source_path else None
        source_path = (
            emoticon.source_path
            if existing_path is not None and existing_path.is_file()
            else self._emoticon_source_path(code, root)
        )
        mime_type = mimetypes.guess_type(source_path)[0] or ''
        if emoticon is None:
            emoticon = QZoneEmoticon.objects.create(
                code=code,
                source_path=source_path,
                mime_type=mime_type,
                status=QZoneEmoticon.STATUS_PENDING if source_path else QZoneEmoticon.STATUS_MISSING,
                error='' if source_path else f'File not found for QQ emoticon: {code}',
            )
            self._emoticons_by_code[code] = emoticon
            return emoticon

        changed = emoticon.source_path != source_path or emoticon.mime_type != mime_type
        if not changed and (
            source_path
            or emoticon.media_asset_id is not None
            or emoticon.status == QZoneEmoticon.STATUS_MISSING
        ):
            return emoticon
        emoticon.source_path = source_path
        emoticon.mime_type = mime_type
        update_fields = ['source_path', 'mime_type', 'updated_at']
        if not source_path:
            if emoticon.media_asset_id is None:
                emoticon.status = QZoneEmoticon.STATUS_MISSING
                emoticon.error = f'File not found for QQ emoticon: {code}'
                update_fields.extend(['status', 'error'])
        elif changed and emoticon.status != QZoneEmoticon.STATUS_READY:
            emoticon.status = QZoneEmoticon.STATUS_PENDING
            emoticon.error = ''
            update_fields.extend(['status', 'error'])
        emoticon.save(update_fields=update_fields)
        return emoticon

    def _prepare_emoticon_links(self, owner, root, through_model, owner_field):
        codes = extract_qzone_emoticon_codes(owner.content_raw)
        links = []
        for code in codes:
            emoticon = self._upsert_emoticon(code, root)
            links.append(through_model(**{
                f'{owner_field}_id': owner.id,
                'qzoneemoticon_id': emoticon.code,
            }))
        return codes, links

    def prepare_media(self, limit=0):
        posts = QZonePost.objects.order_by('id')
        comments = QZoneComment.objects.order_by('id')
        if limit:
            posts = posts[:limit]
            comments = comments[:limit]
        prepared = 0
        emoticon_references = 0
        emoticon_codes = set()
        post_emoticon_links = []
        comment_emoticon_links = []
        for post in posts.iterator(chunk_size=self.batch_size):
            root = self._source_root(post.source_payload)
            for position, item in enumerate(post.media or []):
                self._upsert_media(post=post, position=position, item=item, root=root)
                prepared += 1
            codes, links = self._prepare_emoticon_links(
                post, root, QZonePost.emoticons.through, 'qzonepost',
            )
            emoticon_references += len(codes)
            emoticon_codes.update(codes)
            post_emoticon_links.extend(links)
        for comment in comments.iterator(chunk_size=self.batch_size):
            root = self._source_root(comment.source_payload)
            for position, item in enumerate((comment.source_payload or {}).get('pic') or []):
                normalized = dict(item)
                normalized['type'] = 'image'
                self._upsert_media(comment=comment, position=position, item=normalized, root=root)
                prepared += 1
            codes, links = self._prepare_emoticon_links(
                comment, root, QZoneComment.emoticons.through, 'qzonecomment',
            )
            emoticon_references += len(codes)
            emoticon_codes.update(codes)
            comment_emoticon_links.extend(links)
        QZonePost.emoticons.through.objects.bulk_create(
            post_emoticon_links, batch_size=self.batch_size, ignore_conflicts=True,
        )
        QZoneComment.emoticons.through.objects.bulk_create(
            comment_emoticon_links, batch_size=self.batch_size, ignore_conflicts=True,
        )
        result = {
            'attachments': prepared,
            'emoticon_references': emoticon_references,
            'emoticon_codes': len(emoticon_codes),
            'emoticons_missing': QZoneEmoticon.objects.filter(
                code__in=emoticon_codes,
                status=QZoneEmoticon.STATUS_MISSING,
            ).count(),
        }
        self.write(f'media manifest: {result}')
        return result

    def _upsert_media(self, *, position, item, root, post=None, comment=None):
        kind = str(item.get('type') or 'image').strip().lower()
        if kind not in {'image', 'video', 'audio'}:
            kind = 'image'
        relative_path = str(item.get('custom_filepath') or '').strip().lstrip('/')
        source_path = str(Path(root) / relative_path) if root and relative_path else relative_path
        source_url = str(
            item.get('custom_url') or item.get('url3') or item.get('url2')
            or item.get('o_url') or item.get('b_url') or ''
        )[:1000]
        mime_type = mimetypes.guess_type(relative_path)[0] or ''
        lookup = {'position': position, 'post': post} if post is not None else {'position': position, 'comment': comment}
        media, created = QZoneMedia.objects.get_or_create(
            **lookup,
            defaults={
                'kind': kind,
                'source_path': source_path,
                'source_url': source_url,
                'mime_type': mime_type,
            },
        )
        if created:
            return media
        changed = media.source_path != source_path or media.kind != kind
        media.kind = kind
        media.source_path = source_path
        media.source_url = source_url
        media.mime_type = mime_type
        update_fields = ['kind', 'source_path', 'source_url', 'mime_type', 'updated_at']
        if changed and media.status != QZoneMedia.STATUS_READY:
            media.status = QZoneMedia.STATUS_PENDING
            media.error = ''
            update_fields.extend(['status', 'error'])
        media.save(update_fields=update_fields)
        return media

    @staticmethod
    def _file_digest(path):
        digest = hashlib.sha256()
        size = 0
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _qiniu_client():
        access_key = Config.get_value_by_key(CI.QINIU_ACCESS_KEY, default='')
        secret_key = Config.get_value_by_key(CI.QINIU_SECRET_KEY, default='')
        bucket = Config.get_value_by_key(CI.QINIU_BUCKET, default='')
        if not access_key or not secret_key or not bucket:
            raise CommandError('Qiniu access key, secret key and bucket must be configured.')
        return Auth(access_key, secret_key), bucket

    @staticmethod
    def _media_key(media, content_hash):
        extension = Path(media.source_path).suffix.lower()
        if not re.fullmatch(r'\.[a-z0-9][a-z0-9._+-]{0,31}', extension):
            extension = mimetypes.guess_extension(media.mime_type or '') or '.bin'
        return f'sermo/messages/{media.kind}/{content_hash[:32]}{extension}'

    @staticmethod
    def _emoticon_key(emoticon, content_hash):
        extension = Path(emoticon.source_path).suffix.lower()
        if not re.fullmatch(r'\.[a-z0-9][a-z0-9._+-]{0,31}', extension):
            extension = mimetypes.guess_extension(emoticon.mime_type or '') or '.bin'
        return f'sermo/qzone/emoticon/{content_hash[:32]}{extension}'

    def upload_media(self, limit=0, retry_failed=False):
        statuses = [QZoneMedia.STATUS_PENDING]
        if retry_failed:
            statuses.extend([QZoneMedia.STATUS_FAILED, QZoneMedia.STATUS_MISSING])
        queryset = QZoneMedia.objects.filter(status__in=statuses).order_by('id')
        if limit:
            queryset = queryset[:limit]
        ids = list(queryset.values_list('id', flat=True))
        qiniu_auth = bucket = None
        completed = reused = missing = failed = 0
        for media_id in ids:
            media = QZoneMedia.objects.get(id=media_id)
            path = self.data_root / media.source_path
            if not path.is_file():
                media.status = QZoneMedia.STATUS_MISSING
                media.error = f'File not found: {media.source_path}'[:500]
                media.save(update_fields=['status', 'error', 'updated_at'])
                missing += 1
                continue
            try:
                content_hash, file_size = self._file_digest(path)
                duplicate = MediaAsset.find_duplicate(content_hash, file_size=file_size)
                if duplicate is not None:
                    media.media_asset = duplicate
                    media.content_hash = content_hash
                    media.file_size = file_size
                    media.status = QZoneMedia.STATUS_READY
                    media.error = ''
                    media.save(update_fields=[
                        'media_asset', 'content_hash', 'file_size', 'status', 'error', 'updated_at',
                    ])
                    reused += 1
                    continue
                if qiniu_auth is None:
                    qiniu_auth, bucket = self._qiniu_client()
                key = self._media_key(media, content_hash)
                token = qiniu_auth.upload_token(bucket, key, 3600)
                _result, info = put_file(token, key, str(path), check_crc=True)
                if info.status_code not in (200, 614):
                    raise RuntimeError(f'Qiniu upload failed with status {info.status_code}: {info.text_body}')
                source_uri = avatar_uri_for_key(key)
                try:
                    asset = MediaAsset.objects.create(
                        source_key=key,
                        source_uri=source_uri,
                        original_key=key,
                        original_uri=source_uri,
                        kind=MediaAsset.kind_for_name(media.kind),
                        content_hash=content_hash,
                        mime_type=(media.mime_type or mimetypes.guess_type(path.name)[0] or '')[:100],
                        file_size=file_size,
                        status=MediaAsset.STATUS_PENDING if media.kind in {'image', 'video'} else MediaAsset.STATUS_READY,
                        geocoding_status=(
                            MediaAsset.GEOCODING_PENDING
                            if media.kind in {'image', 'video'}
                            else MediaAsset.GEOCODING_UNAVAILABLE
                        ),
                        raw_metadata={'source': 'qzone_import', 'source_path': media.source_path},
                    )
                except IntegrityError:
                    asset = MediaAsset.find_duplicate(content_hash, file_size=file_size)
                    if asset is None:
                        asset = MediaAsset.objects.get(source_key=key)
                media.media_asset = asset
                media.content_hash = content_hash
                media.file_size = file_size
                media.status = QZoneMedia.STATUS_READY
                media.error = ''
                media.save(update_fields=[
                    'media_asset', 'content_hash', 'file_size', 'status', 'error', 'updated_at',
                ])
                completed += 1
            except Exception as error:
                media.status = QZoneMedia.STATUS_FAILED
                media.error = str(error)[:500]
                media.save(update_fields=['status', 'error', 'updated_at'])
                failed += 1
        emoticon_stats = {'uploaded': 0, 'reused': 0, 'missing': 0, 'failed': 0}
        emoticon_statuses = [QZoneEmoticon.STATUS_PENDING]
        if retry_failed:
            emoticon_statuses.extend([QZoneEmoticon.STATUS_FAILED, QZoneEmoticon.STATUS_MISSING])
        emoticons = QZoneEmoticon.objects.filter(status__in=emoticon_statuses).order_by('code')
        if limit:
            emoticons = emoticons[:limit]
        for code in list(emoticons.values_list('code', flat=True)):
            emoticon = QZoneEmoticon.objects.get(code=code)
            path = self.data_root / emoticon.source_path
            if not emoticon.source_path or not path.is_file():
                emoticon.status = QZoneEmoticon.STATUS_MISSING
                emoticon.error = f'File not found: {emoticon.source_path or emoticon.code}'[:500]
                emoticon.save(update_fields=['status', 'error', 'updated_at'])
                emoticon_stats['missing'] += 1
                missing += 1
                continue
            try:
                content_hash, file_size = self._file_digest(path)
                duplicate = MediaAsset.find_duplicate(content_hash, file_size=file_size)
                if duplicate is not None:
                    emoticon.media_asset = duplicate
                    emoticon.content_hash = content_hash
                    emoticon.file_size = file_size
                    emoticon.status = QZoneEmoticon.STATUS_READY
                    emoticon.error = ''
                    emoticon.save(update_fields=[
                        'media_asset', 'content_hash', 'file_size', 'status', 'error', 'updated_at',
                    ])
                    emoticon_stats['reused'] += 1
                    reused += 1
                    continue
                if qiniu_auth is None:
                    qiniu_auth, bucket = self._qiniu_client()
                key = self._emoticon_key(emoticon, content_hash)
                token = qiniu_auth.upload_token(bucket, key, 3600)
                _result, info = put_file(token, key, str(path), check_crc=True)
                if info.status_code not in (200, 614):
                    raise RuntimeError(f'Qiniu upload failed with status {info.status_code}: {info.text_body}')
                source_uri = avatar_uri_for_key(key)
                try:
                    asset = MediaAsset.objects.create(
                        source_key=key,
                        source_uri=source_uri,
                        original_key=key,
                        original_uri=source_uri,
                        kind=MediaAsset.KIND_IMAGE,
                        content_hash=content_hash,
                        mime_type=(emoticon.mime_type or mimetypes.guess_type(path.name)[0] or '')[:100],
                        file_size=file_size,
                        status=MediaAsset.STATUS_READY,
                        geocoding_status=MediaAsset.GEOCODING_UNAVAILABLE,
                        raw_metadata={
                            'source': 'qzone_emoticon_import',
                            'source_path': emoticon.source_path,
                            'code': emoticon.code,
                        },
                    )
                except IntegrityError:
                    asset = MediaAsset.find_duplicate(content_hash, file_size=file_size)
                    if asset is None:
                        asset = MediaAsset.objects.get(source_key=key)
                emoticon.media_asset = asset
                emoticon.content_hash = content_hash
                emoticon.file_size = file_size
                emoticon.status = QZoneEmoticon.STATUS_READY
                emoticon.error = ''
                emoticon.save(update_fields=[
                    'media_asset', 'content_hash', 'file_size', 'status', 'error', 'updated_at',
                ])
                emoticon_stats['uploaded'] += 1
                completed += 1
            except Exception as error:
                emoticon.status = QZoneEmoticon.STATUS_FAILED
                emoticon.error = str(error)[:500]
                emoticon.save(update_fields=['status', 'error', 'updated_at'])
                emoticon_stats['failed'] += 1
                failed += 1
        result = {
            'uploaded': completed,
            'reused': reused,
            'missing': missing,
            'failed': failed,
            'emoticons': emoticon_stats,
        }
        self.write(f'media upload: {result}')
        return result

    def project(self, limit=0):
        self.space.require_qq_binding_granted()
        identity_by_qq = dict(
            QQIdentity.objects.filter(space=self.space).values_list('qq', 'user_id')
        )
        required_qqs = set(QZonePost.objects.values_list('author_id', flat=True))
        required_qqs.update(QZoneComment.objects.values_list('author_id', flat=True))
        missing_identities = required_qqs - set(identity_by_qq)
        if missing_identities:
            raise CommandError(f'Run identity stage first; {len(missing_identities)} QQ identities are missing.')
        posts = QZonePost.objects.select_related('statement').prefetch_related(
            'media_items__media_asset', 'emoticons__media_asset',
        ).order_by('published_at', 'id')
        if limit:
            posts = posts[:limit]
        post_count = self._project_posts(posts, identity_by_qq)
        comments = QZoneComment.objects.select_related('post', 'statement_comment').prefetch_related(
            'media_items__media_asset', 'emoticons__media_asset',
        ).order_by('id')
        if limit:
            comments = comments[:limit]
        comment_count = self._project_comments(comments, identity_by_qq)
        result = {'posts_created': post_count, 'comments_created': comment_count}
        self.write(f'projection: {result}')
        return result

    def _project_posts(self, queryset, identity_by_qq):
        created = 0
        for source in queryset.iterator(chunk_size=self.batch_size):
            ready_media = [item for item in source.media_items.all() if item.media_asset_id]
            text = normalize_qzone_text(source.content_raw or source.content_text)
            if source.visibility != 'public' or (not text and not ready_media):
                continue
            with transaction.atomic():
                if source.statement_id is None:
                    statement = Statement.objects.create(
                        space=self.space,
                        user_id=identity_by_qq[source.author_id],
                        text=text,
                        visibility=StatementVisibilityChoice.PUBLIC,
                    )
                    Statement.objects.filter(id=statement.id).update(created_at=source.published_at)
                    statement.created_at = source.published_at
                    source.statement = statement
                    source.save(update_fields=['statement'])
                    created += 1
                else:
                    statement = source.statement
                    if statement.text != text:
                        statement.text = text
                        statement.save(update_fields=['text'])
                existing_positions = set(statement.media.values_list('position', flat=True))
                StatementMedia.objects.bulk_create([
                    StatementMedia(statement=statement, media_asset=item.media_asset, position=item.position)
                    for item in ready_media
                    if item.position not in existing_positions
                ], ignore_conflicts=True)
        return created

    def _project_comments(self, queryset, identity_by_qq):
        created = 0
        projected_by_source_id = dict(
            QZoneComment.objects.exclude(statement_comment_id=None).values_list('id', 'statement_comment_id')
        )
        for source in queryset.iterator(chunk_size=self.batch_size):
            if source.post.statement_id is None:
                continue
            ready_media = [item for item in source.media_items.all() if item.media_asset_id]
            text = normalize_qzone_text(source.content_raw)
            if not text and not ready_media:
                continue
            parent_id = projected_by_source_id.get(source.parent_id) if source.parent_id else None
            if source.parent_id and parent_id is None:
                continue
            reply_to_user_id = identity_by_qq.get(source.reply_to_id) if source.reply_to_id else None
            with transaction.atomic():
                if source.statement_comment_id is None:
                    comment = StatementComment.objects.create(
                        statement_id=source.post.statement_id,
                        user_id=identity_by_qq[source.author_id],
                        parent_id=parent_id,
                        reply_to_user_id=reply_to_user_id,
                        text=text,
                    )
                    StatementComment.objects.filter(id=comment.id).update(created_at=source.published_at)
                    comment.created_at = source.published_at
                    source.statement_comment = comment
                    source.save(update_fields=['statement_comment'])
                    projected_by_source_id[source.id] = comment.id
                    created += 1
                else:
                    comment = source.statement_comment
                    if comment.text != text:
                        comment.text = text
                        comment.save(update_fields=['text'])
                existing_positions = set(comment.media.values_list('position', flat=True))
                StatementCommentMedia.objects.bulk_create([
                    StatementCommentMedia(comment=comment, media_asset=item.media_asset, position=item.position)
                    for item in ready_media
                    if item.position not in existing_positions
                ], ignore_conflicts=True)
        return created

    def verify(self):
        report = {
            'source_users': QZoneUser.objects.count(),
            'source_posts': QZonePost.objects.count(),
            'source_comments': QZoneComment.objects.count(),
            'qq_identities': QQIdentity.objects.filter(space=self.space).count(),
            'projected_posts': QZonePost.objects.exclude(statement_id=None).count(),
            'projected_comments': QZoneComment.objects.exclude(statement_comment_id=None).count(),
            'media_total': QZoneMedia.objects.count(),
            'media_ready': QZoneMedia.objects.filter(status=QZoneMedia.STATUS_READY).count(),
            'media_pending': QZoneMedia.objects.filter(status=QZoneMedia.STATUS_PENDING).count(),
            'media_missing': QZoneMedia.objects.filter(status=QZoneMedia.STATUS_MISSING).count(),
            'media_failed': QZoneMedia.objects.filter(status=QZoneMedia.STATUS_FAILED).count(),
            'emoticons_total': QZoneEmoticon.objects.count(),
            'emoticons_ready': QZoneEmoticon.objects.filter(status=QZoneEmoticon.STATUS_READY).count(),
            'emoticons_pending': QZoneEmoticon.objects.filter(status=QZoneEmoticon.STATUS_PENDING).count(),
            'emoticons_missing': QZoneEmoticon.objects.filter(status=QZoneEmoticon.STATUS_MISSING).count(),
            'emoticons_failed': QZoneEmoticon.objects.filter(status=QZoneEmoticon.STATUS_FAILED).count(),
            'projected_comment_without_projected_post': QZoneComment.objects.exclude(
                statement_comment_id=None,
            ).filter(post__statement_id=None).count(),
            'projected_reply_without_parent': QZoneComment.objects.exclude(
                statement_comment_id=None,
            ).filter(parent_id__isnull=False, parent__statement_comment_id=None).count(),
        }
        self.write(json.dumps(report, ensure_ascii=False, indent=2))
        return report


def resolve_space(slug):
    try:
        return Space.objects.get(slug=str(slug or '').strip().lower())
    except Space.DoesNotExist as error:
        raise CommandError(f'Space does not exist: {slug}') from error
