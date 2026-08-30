import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from qiniu import Auth, PersistentFop, urlsafe_base64_encode

from Config.models import Config, CI
from Message.models import MediaAsset
from utils.qiniu import avatar_uri_for_key, delete_message_media_file


VIDEO_TRANSCODE_FOPS = 'avthumb/mp4/vcodec/libx264/s/1280x720/autoscale/1/vb/1400k/r/30/acodec/aac/ab/96k'
ORIGINAL_RETENTION = timedelta(days=7)


def _client():
    auth = Auth(Config.get_value_by_key(CI.QINIU_ACCESS_KEY), Config.get_value_by_key(CI.QINIU_SECRET_KEY))
    bucket = Config.get_value_by_key(CI.QINIU_BUCKET)
    pipeline = Config.get_value_by_key(CI.QINIU_VIDEO_PIPELINE, default='') or None
    return bucket, PersistentFop(auth, bucket, pipeline)


def submit_video_transcode(asset_id):
    eligible = {MediaAsset.TRANSCODE_NONE, MediaAsset.TRANSCODE_FAILED}
    asset = MediaAsset.objects.filter(id=asset_id, kind=MediaAsset.KIND_VIDEO, transcode_status__in=eligible).first()
    if asset is None:
        return asset
    original_key = asset.original_key or asset.source_key
    original_uri = asset.original_uri or asset.source_uri
    playback_key = asset.playback_key or f'sermo/messages/video-playback/{uuid.uuid4().hex}.mp4'
    claimed = MediaAsset.objects.filter(id=asset.id, transcode_status__in=eligible).update(
        original_key=original_key, original_uri=original_uri,
        playback_key=playback_key, transcode_status=MediaAsset.TRANSCODE_PENDING,
        transcode_error='',
    )
    if not claimed:
        return MediaAsset.objects.get(id=asset.id)
    try:
        bucket, client = _client()
        operation = f'{VIDEO_TRANSCODE_FOPS}|saveas/{urlsafe_base64_encode(f"{bucket}:{playback_key}")}'
        result, info = client.execute(original_key, [operation], 0)
        persistent_id = (result or {}).get('persistentId')
        if not persistent_id:
            raise RuntimeError(getattr(info, 'text_body', None) or 'missing persistentId')
        MediaAsset.objects.filter(id=asset.id).update(
            transcode_persistent_id=persistent_id, transcode_error='',
        )
    except Exception as error:
        MediaAsset.objects.filter(id=asset.id).update(
            original_key=original_key, original_uri=original_uri,
            playback_key=playback_key, transcode_status=MediaAsset.TRANSCODE_FAILED,
            transcode_error=str(error)[:500],
        )
    return MediaAsset.objects.get(id=asset.id)


def refresh_video_transcode(asset_id):
    asset = MediaAsset.objects.filter(id=asset_id, transcode_status=MediaAsset.TRANSCODE_PENDING).first()
    if asset is None:
        return None
    try:
        _bucket, client = _client()
        result, info = client.get_status(asset.transcode_persistent_id)
        code = int((result or {}).get('code', -1))
        if code in {1, 2}:
            return asset
        if code != 0:
            raise RuntimeError((result or {}).get('desc') or getattr(info, 'text_body', None) or f'pfop code {code}')
        playback_uri = avatar_uri_for_key(asset.playback_key)
        with transaction.atomic():
            MediaAsset.objects.filter(id=asset.id).update(
                source_key=asset.playback_key, source_uri=playback_uri,
                playback_uri=playback_uri, mime_type='video/mp4',
                transcode_status=MediaAsset.TRANSCODE_READY, transcoded_at=timezone.now(),
                transcode_error='',
            )
        MediaAsset.refresh(MediaAsset.objects.get(id=asset.id), geocode=False)
    except Exception as error:
        MediaAsset.objects.filter(id=asset.id).update(
            transcode_status=MediaAsset.TRANSCODE_FAILED, transcode_error=str(error)[:500],
        )
    return MediaAsset.objects.get(id=asset.id)


def delete_expired_original(asset_id):
    asset = MediaAsset.objects.filter(id=asset_id, transcode_status=MediaAsset.TRANSCODE_READY, original_deleted_at__isnull=True).first()
    if asset is None or not asset.transcoded_at or asset.transcoded_at > timezone.now() - ORIGINAL_RETENTION:
        return False
    if not asset.original_key or asset.original_key == asset.source_key:
        return False
    delete_message_media_file(asset.original_key)
    MediaAsset.objects.filter(id=asset.id).update(original_deleted_at=timezone.now(), original_uri='')
    return True
