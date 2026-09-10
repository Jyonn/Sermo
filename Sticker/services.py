from Message.models import Message, MessageTypeChoice
from Sticker.models import UserSticker
from utils.qiniu import delete_sticker_file


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
    asset.delete()
    if storage_key.startswith('sermo/messages/sticker/'):
        delete_sticker_file(storage_key)
    return True
