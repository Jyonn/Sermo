from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error


@Error.register
class StickerErrors:
    NOT_FOUND = Error(message=_('Sticker does not exist'), code=Code.NotFound)
    INVALID_HASH = Error(message=_('Invalid sticker hash'), code=Code.BadRequest)
    INVALID_IMAGE = Error(message=_('Only image messages can be added as stickers'), code=Code.BadRequest)
    NOT_ACCESSIBLE = Error(message=_('You cannot access this sticker'), code=Code.Forbidden)
    DOWNLOAD_FAILED = Error(message=_('Unable to collect this image'), code=Code.BadRequest)
