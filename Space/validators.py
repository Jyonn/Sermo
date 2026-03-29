from django.utils.translation import gettext_lazy as _

from smartdjango import Error, Code


RESERVED_SLUGS = {
    'api', 'www', 'admin', 'static', 'cdn', 'mail', 'smtp', 'imap', 'pop',
    'ftp', 'docs', 'status', 'support', 'help', 'blog', 'dev', 'test', 'staging'
}


@Error.register
class SpaceErrors:
    NOT_EXISTS = Error(message=_('Space ({attr}={value}) does not exist'), code=Code.NotFound)
    NAME_EMPTY = Error(message=_('Space name cannot be empty'), code=Code.BadRequest)
    NAME_TOO_LONG = Error(message=_('Space name is too long'), code=Code.BadRequest)
    SLUG_TOO_SHORT = Error(message=_('Space slug should be at least {min_length} characters long'), code=Code.BadRequest)
    SLUG_INVALID = Error(message=_('Space slug can only contain lowercase letters, numbers and hyphens'), code=Code.BadRequest)
    SLUG_TAKEN = Error(message=_('Space slug is already taken'), code=Code.BadRequest)
    SLUG_RESERVED = Error(message=_('Space slug is reserved'), code=Code.BadRequest)
    EMAIL_REQUIRED = Error(message=_('Space email is required'), code=Code.BadRequest)
    EMAIL_TAKEN = Error(message=_('Space email is already taken'), code=Code.BadRequest)
    EMAIL_CODE_INVALID = Error(message=_('Invalid space email verification code'), code=Code.BadRequest)
    EMAIL_CODE_EXPIRED = Error(message=_('Space email verification code expired'), code=Code.BadRequest)
    EMAIL_SEND_FAILED = Error(message=_('Failed to send space verification email'), code=Code.InternalServerError)
    EMAIL_MISMATCH = Error(message=_('Space email does not match'), code=Code.BadRequest)
    MEMBER_LIMIT_INVALID = Error(message=_('Member limit should be at least 1'), code=Code.BadRequest)
    MEMBER_LIMIT_TOO_LOW = Error(message=_('Member limit cannot be lower than current member count'), code=Code.BadRequest)
    MEMBER_LIMIT_REACHED = Error(message=_('This space has reached its member limit'), code=Code.BadRequest)
    NOTIFICATOR_FAILED = Error(message=_('Failed to send notification'), code=Code.InternalServerError)


class SpaceValidator:
    NAME_MAX_LENGTH = 20
    SLUG_MAX_LENGTH = 15
    SLUG_MIN_LENGTH = 3
    MEMBER_LIMIT_MAX = 10000

    @classmethod
    def name(cls, value):
        normalized = (value or '').strip()
        if not normalized:
            raise SpaceErrors.NAME_EMPTY
        if len(normalized) > cls.NAME_MAX_LENGTH:
            raise SpaceErrors.NAME_TOO_LONG
        return normalized

    @classmethod
    def slug(cls, value):
        if len(value) < cls.SLUG_MIN_LENGTH:
            raise SpaceErrors.SLUG_TOO_SHORT(min_length=cls.SLUG_MIN_LENGTH)
        allow_string = 'abcdefghijklmnopqrstuvwxyz0123456789-'
        if not all(c in allow_string for c in value):
            raise SpaceErrors.SLUG_INVALID

    @classmethod
    def reserved_slug(cls, value):
        return value in RESERVED_SLUGS

    @classmethod
    def member_limit(cls, value):
        if value is None:
            return None
        member_limit = int(value)
        if member_limit < 1 or member_limit > cls.MEMBER_LIMIT_MAX:
            raise SpaceErrors.MEMBER_LIMIT_INVALID
        return member_limit
