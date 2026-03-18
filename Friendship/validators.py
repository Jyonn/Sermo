from django.utils.translation import gettext_lazy as _
from smartdjango import Error, Code


@Error.register
class FriendshipErrors:
    NOT_EXISTS = Error(message=_('Friendship ({attr}={value}) does not exist'), code=Code.NotFound)
    INVALID_TARGET = Error(message=_('Invalid friendship target'), code=Code.BadRequest)
    UNALIGNED_SPACE = Error(message=_('Users are not in the same space'), code=Code.Forbidden)
    REQUEST_FORBIDDEN = Error(message=_('You are not allowed to request friendship'), code=Code.Forbidden)
    REQUEST_EXISTS = Error(message=_('A pending friendship request already exists'), code=Code.BadRequest)
    REQUEST_CLOSED = Error(message=_('This friendship request is not pending'), code=Code.BadRequest)
    ALREADY_FRIENDS = Error(message=_('Users are already friends'), code=Code.BadRequest)
    NOT_FRIENDS = Error(message=_('Users are not friends'), code=Code.BadRequest)
    LOCKED_FORBIDDEN = Error(message=_('This friendship is locked and cannot be removed'), code=Code.Forbidden)
    INVITE_TOKEN_INVALID = Error(message=_('Invalid friend invite token'), code=Code.BadRequest)
    INVITE_TOKEN_EXPIRED = Error(message=_('Friend invite token expired'), code=Code.BadRequest)
    INVITE_TOKEN_SPACE_MISMATCH = Error(message=_('Invite token does not belong to your space'), code=Code.Forbidden)
    INVITE_TOKEN_SECRET_MISSING = Error(message=_('Invite token secret key is not configured'), code=Code.InternalServerError)


class FriendshipValidator:
    pass
