from collections import Counter

from django.core import signing
from django.utils import timezone

from Message.models import MediaAsset, LinkPreview
from Square.models import StatementMedia, StatementMediaKindChoice, StatementRevision, StatementVisibilityChoice
from Square.validators import SquareErrors


UPLOAD_SALT = 'square.statement.edit-upload'
MEDIA_KINDS = {
    MediaAsset.KIND_IMAGE: StatementMediaKindChoice.IMAGE,
    MediaAsset.KIND_AUDIO: StatementMediaKindChoice.AUDIO,
    MediaAsset.KIND_VIDEO: StatementMediaKindChoice.VIDEO,
}
MEDIA_CAPABILITIES = {
    StatementMediaKindChoice.IMAGE: 'square.statement.publish.image',
    StatementMediaKindChoice.AUDIO: 'square.statement.publish.audio',
    StatementMediaKindChoice.VIDEO: 'square.statement.publish.video',
}


def upload_claim(user, key, kind):
    return dict(user_id=user.id, space_id=user.space_id, key=key, kind=kind)


def issue_edit_upload_grant(user, key, kind):
    return signing.dumps(upload_claim(user, key, kind), salt=UPLOAD_SALT)


def _editable_media(user, original, desired):
    existing = {row.id: row for row in original}
    result = []
    new_kinds = set()
    counts = Counter()
    for item in desired:
        if 'media_id' in item:
            media_id = item['media_id']
            if type(media_id) is not int or media_id not in existing or item.keys() != {'media_id'}:
                raise SquareErrors.MEDIA_INVALID
            retained = existing.pop(media_id)
            kind = MEDIA_KINDS[retained.media_asset.kind]
            result.append(retained)
        else:
            (upload,) = StatementMedia.normalize_payload([item])
            grant = item.get('edit_upload_grant')
            try:
                claim = signing.loads(grant, salt=UPLOAD_SALT, max_age=3600) if isinstance(grant, str) and len(grant) <= 512 else None
            except signing.BadSignature:
                claim = None
            if claim != upload_claim(user, upload['key'], str(item.get('kind') or '').strip().lower()):
                raise SquareErrors.MEDIA_INVALID
            kind = upload['kind']
            new_kinds.add(kind)
            result.append(upload)
        counts[kind] += 1
    if counts[StatementMediaKindChoice.IMAGE] > 9:
        raise SquareErrors.IMAGE_LIMIT_EXCEEDED
    if counts[StatementMediaKindChoice.AUDIO] > 1:
        raise SquareErrors.AUDIO_LIMIT_EXCEEDED
    if counts[StatementMediaKindChoice.VIDEO] > 1:
        raise SquareErrors.VIDEO_LIMIT_EXCEEDED
    if len(counts) > 1:
        raise SquareErrors.MEDIA_INVALID
    for kind in new_kinds:
        user.require_capability(MEDIA_CAPABILITIES[kind])
    return result


def _replace_media(statement, desired):
    retained = [item.id for item in desired if isinstance(item, StatementMedia)]
    statement.media.exclude(id__in=retained).delete()
    uploads = [item for item in desired if isinstance(item, dict)]
    StatementMedia.attach_assets(uploads)
    added = []
    moved = []
    for position, item in enumerate(desired):
        if isinstance(item, dict):
            added.append(StatementMedia(statement=statement, position=position, media_asset=item['media_asset']))
        elif item.position != position:
            item.position = position
            moved.append(item)
    StatementMedia.objects.bulk_create(added)
    StatementMedia.objects.bulk_update(moved, ['position'])


def edit_statement(statement, user, text, revision, content):
    """Apply one revision to an author-owned row locked by the caller."""
    if revision != statement.edit_revision:
        raise SquareErrors.EDIT_CONFLICT
    if text is None:
        text = statement.text

    audience = content.get('visibility', statement.audience)
    if statement.is_anonymous and audience != 'public':
        raise SquareErrors.ANONYMOUS_VISIBILITY_INVALID
    location = content.get('location', statement.location_payload)
    keep_record = content.get('keep_chat_record', True)
    record_id = statement.forward_bundle_id if keep_record else None
    existing = list(statement.media.select_related('media_asset'))
    media = _editable_media(user, existing, content['media']) if 'media' in content else existing
    previous_preview = statement.link_preview if statement.link_preview_id else None
    previous_url = previous_preview.url if previous_preview else None
    url = content.get('external_media_url', previous_url)
    if url and (media or record_id):
        raise SquareErrors.EXTERNAL_MEDIA_EXCLUSIVE
    if record_id and media:
        raise SquareErrors.CHAT_RECORD_EXCLUSIVE
    if not (text or media or url or record_id):
        raise SquareErrors.CONTENT_REQUIRED
    if text != statement.text and not (media or url or record_id):
        user.require_capability('square.statement.publish.text')

    changed = []
    if text != statement.text:
        changed.append('text')
    if audience != statement.audience:
        changed.append('visibility')
    if location != statement.location_payload:
        changed.append('location')
    if media != existing:
        changed.append('media')
    if url != previous_url:
        changed.append('external_media')
    if record_id != statement.forward_bundle_id:
        changed.append('chat_record')
    if not changed:
        return

    preview = previous_preview
    if 'external_media' in changed:
        preview = LinkPreview.queue_for_text(url, requester=user, initial=True) if url else None
        if url and preview is None:
            raise SquareErrors.EXTERNAL_MEDIA_INVALID

    if 'visibility' in changed:
        # Versions from the text-only editor inherited the post's previous audience.
        statement.revisions.filter(snapshot__isnull=True).update(snapshot={'visibility': statement.audience})
    StatementRevision.objects.create(
        statement=statement, revision=revision, text=statement.text,
        published_at=statement.edited_at or statement.created_at,
        snapshot={'visibility': statement.audience, 'changes': changed},
    )
    statement.text = text
    statement.visibility = StatementVisibilityChoice.FRIENDS if audience == 'friends' else StatementVisibilityChoice.PUBLIC
    statement.latitude = location['latitude'] if location else None
    statement.longitude = location['longitude'] if location else None
    statement.address = location['address'] if location else ''
    statement.geocoding_provider = location['geocoding_provider'] if location else ''
    statement.forward_bundle_id = record_id
    if not record_id:
        statement.chat_record_redacted = False
    statement.link_preview = preview
    statement.edit_revision += 1
    statement.edited_at = timezone.now()
    statement.save(update_fields=[
        'text', 'visibility', 'latitude', 'longitude', 'address', 'geocoding_provider',
        'forward_bundle', 'chat_record_redacted', 'link_preview', 'edit_revision', 'edited_at',
    ])
    if 'media' in changed:
        _replace_media(statement, media)
