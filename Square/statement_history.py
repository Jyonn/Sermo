from django.db.models import Prefetch

from Message.models import ForwardBundle, ForwardBundleItem, LinkPreview, MediaAsset
from Square.models import _qzone_inline_emoticons
from utils.qiniu import (
    build_message_image_display_uri,
    build_message_image_thumbnail_uri,
    build_message_video_thumbnail_uri,
    sign_private_download_url,
)


CONTENT_FIELDS = ('location', 'media', 'external_media', 'chat_record')
MEDIA_HISTORY_TTL = 15 * 60
MEDIA_KINDS = {
    MediaAsset.KIND_IMAGE: 'image', MediaAsset.KIND_VIDEO: 'video', MediaAsset.KIND_AUDIO: 'audio',
}


def statement_history_snapshot(statement, media):
    """Store versioned fields and stable references without delivery URLs."""
    return {
        'visibility': statement.audience,
        'location': statement.location_payload,
        'media': [{'media_id': item.pk, 'asset_id': item.media_asset_id} for item in media],
        'external_media': statement.link_preview_id,
        'chat_record': (
            {'bundle_id': statement.forward_bundle_id, 'redacted': statement.chat_record_redacted}
            if statement.forward_bundle_id else None
        ),
    }


def _history_snapshots(statement, current, revisions):
    snapshots = {row.revision: row.snapshot or {} for row in revisions}
    legacy = [revision for revision, snapshot in snapshots.items()
              if any(field not in snapshot for field in CONTENT_FIELDS)]
    if not legacy:
        return snapshots

    # A legacy row tells us what changed on the way to its next version. Only
    # inherit a value across a contiguous transition that left that field alone.
    newer = current
    expected = statement.edit_revision - 1
    wanted = set(legacy)
    chain = statement.revisions.filter(revision__gte=min(legacy), revision__lte=expected)
    for revision, raw_snapshot in chain.order_by('-revision').values_list('revision', 'snapshot').iterator():
        snapshot = raw_snapshot or {}
        if revision != expected:
            newer = {}
        changes = snapshot.get('changes', ())
        recovered = {
            field: newer[field] for field in CONTENT_FIELDS if field not in changes and field in newer
        }
        newer = {**recovered, **snapshot, 'visibility': snapshot.get('visibility', statement.audience)}
        if revision in wanted:
            snapshots[revision] = newer
        expected = revision - 1
    return snapshots


def _history_media(reference, asset):
    expires = {'expire_seconds': MEDIA_HISTORY_TTL}
    if asset.kind == MediaAsset.KIND_IMAGE:
        uri = build_message_image_display_uri(asset.source_uri, asset.mime_type, **expires)
        thumbnail = build_message_image_thumbnail_uri(asset.source_uri, width=480, **expires)
    else:
        uri = sign_private_download_url(asset.source_uri, **expires)
        thumbnail = build_message_video_thumbnail_uri(asset.source_uri, **expires) if asset.kind == MediaAsset.KIND_VIDEO else None
    return dict(media_id=reference['media_id'], kind=MEDIA_KINDS[asset.kind], uri=uri,
                thumbnail_uri=thumbnail, duration_seconds=asset.duration_seconds,
                mime_type=asset.mime_type, metadata=asset.jsonl())


def statement_history_payload(statement, revisions, *, include_current, request):
    current = statement_history_snapshot(statement, statement.media.all())
    snapshots = _history_snapshots(statement, current, revisions)
    versions = [(row.jsonl(), snapshots[row.revision]) for row in revisions]
    if include_current:
        versions.insert(0, (dict(
            revision=statement.edit_revision, text=statement.text,
            published_at=(statement.edited_at or statement.created_at).timestamp(),
        ), current))

    assets = MediaAsset.objects.in_bulk({
        entry['asset_id'] for _, snapshot in versions for entry in snapshot.get('media', ())
    })
    previews = LinkPreview.objects.in_bulk({
        snapshot['external_media'] for _, snapshot in versions if snapshot.get('external_media')
    })
    bundles = ForwardBundle.objects.select_related('created_by').prefetch_related(Prefetch(
        'items', queryset=ForwardBundleItem.objects.select_related('media_resource__asset'),
    )).in_bulk({
        snapshot['chat_record']['bundle_id'] for _, snapshot in versions if snapshot.get('chat_record')
    })
    emoticons = _qzone_inline_emoticons(statement, request=request)

    for row, snapshot in versions:
        unavailable = [field for field in CONTENT_FIELDS if field not in snapshot]
        media = []
        for item in snapshot.get('media', ()):
            asset = assets.get(item['asset_id'])
            if asset is not None and asset.kind in MEDIA_KINDS:
                media.append(_history_media(item, asset))
        if len(media) < len(snapshot.get('media', ())):
            unavailable.append('media')
        preview_id = snapshot.get('external_media')
        preview = previews.get(preview_id)
        if preview_id and preview is None:
            unavailable.append('external_media')
        record = snapshot.get('chat_record')
        bundle = bundles.get(record['bundle_id']) if record else None
        if record and bundle is None:
            unavailable.append('chat_record')
        row.update(
            visibility=snapshot.get('visibility', statement.audience), location=snapshot.get('location'),
            media=media, external_media=preview.jsonl() if preview else None,
            chat_record=bundle.jsonl(request=request, redact_identity=record['redacted']) if bundle else None,
            unavailable_fields=unavailable, inline_emoticons=emoticons,
        )
    return [row for row, _ in versions]
