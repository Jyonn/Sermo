from Message.models import Message, MessageTypeChoice
from Sticker.models import UserSticker
from utils.qiniu import delete_sticker_file, validate_message_media_key


def delete_unreferenced_sticker_asset(asset):
    """Delete an asset when no collection or visible message still references it."""
    if UserSticker.objects.filter(asset=asset).exists():
        return False
    if Message.objects.filter(
        type=MessageTypeChoice.STICKER,
        is_deleted=False,
        content=f'{{"kind":"sticker","asset_id":{asset.id}}}',
    ).exists():
        return False

    storage_key = asset.storage_key
    try:
        managed_storage_key = validate_message_media_key('sticker', storage_key)
    except Exception:
        managed_storage_key = None
    if managed_storage_key is not None:
        delete_sticker_file(storage_key)
    asset.delete()
    return True
