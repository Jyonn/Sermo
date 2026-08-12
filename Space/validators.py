from django.utils.translation import gettext_lazy as _

from smartdjango import Error, Code

from utils.space_slug import is_reserved_space_slug


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
    EMAIL_TRIAL_SPACE_EXISTS = Error(message=_('Verify the administrator phone for the existing space before creating another one'), code=Code.Forbidden)
    EMAIL_CODE_INVALID = Error(message=_('Invalid space email verification code'), code=Code.BadRequest)
    EMAIL_CODE_EXPIRED = Error(message=_('Space email verification code expired'), code=Code.BadRequest)
    EMAIL_SEND_FAILED = Error(message=_('Failed to send space verification email'), code=Code.InternalServerError)
    EMAIL_MISMATCH = Error(message=_('Space email does not match'), code=Code.BadRequest)
    MEMBER_LIMIT_INVALID = Error(message=_('Member limit should be at least 1'), code=Code.BadRequest)
    MEMBER_LIMIT_TOO_LOW = Error(message=_('Member limit cannot be lower than current member count'), code=Code.BadRequest)
    MEMBER_LIMIT_REACHED = Error(message=_('This space has reached its member limit'), code=Code.BadRequest)
    MEMBER_LIMIT_TIER_EXCEEDED = Error(message=_('The member limit exceeds the current verification tier'), code=Code.BadRequest)
    PHONE_REQUIRED = Error(message=_('Administrator phone is required'), code=Code.BadRequest)
    PHONE_CODE_INVALID = Error(message=_('Invalid administrator phone verification code'), code=Code.BadRequest)
    PHONE_CODE_EXPIRED = Error(message=_('Administrator phone verification code expired'), code=Code.BadRequest)
    PHONE_ALREADY_VERIFIED = Error(message=_('Administrator phone is already verified'), code=Code.BadRequest)
    IDENTITY_FILE_INVALID = Error(message=_('Identity credential must be a PDF file no larger than 10 MB'), code=Code.BadRequest)
    IDENTITY_ALREADY_SUBMITTED = Error(message=_('Identity credential has already been submitted'), code=Code.BadRequest)
    TIER_FEATURE_RESTRICTED = Error(message=_('Verify the administrator phone to unlock this feature'), code=Code.Forbidden)
    LEVEL_NAMES_INVALID = Error(message=_('Space level names are invalid'), code=Code.BadRequest)
    ADMIN_ACCESS_FORBIDDEN = Error(message=_('Only the official account can access space administration'), code=Code.Forbidden)
    NOTIFICATOR_FAILED = Error(message=_('Failed to send notification'), code=Code.InternalServerError)
    MODULES_REQUIRED = Error(message=_('Chat and square cannot both be disabled'), code=Code.BadRequest)
    CHAT_DISABLED = Error(message=_('Chat is disabled in this space'), code=Code.Forbidden)
    SQUARE_DISABLED = Error(message=_('Square is disabled in this space'), code=Code.Forbidden)
    SQUARE_EXPLORE_DISABLED = Error(message=_('Square exploration is disabled in this space'), code=Code.Forbidden)
    UNVERIFIED_GROUP_JOIN_DISABLED = Error(message=_('Unverified members cannot join group chats in this space'), code=Code.Forbidden)
    UNVERIFIED_GROUP_SEND_DISABLED = Error(message=_('Unverified members cannot send messages in group chats in this space'), code=Code.Forbidden)


class SpaceValidator:
    NAME_MAX_LENGTH = 20
    SLUG_MAX_LENGTH = 15
    SLUG_MIN_LENGTH = 3
    MEMBER_LIMIT_MAX = 10000
    LEVEL_COUNT = 18
    LEVEL_NAME_MAX_LENGTH = 8
    UNVERIFIED_GROUP_POLICY_MIN = 0
    UNVERIFIED_GROUP_POLICY_MAX = 2

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
        return is_reserved_space_slug(value)

    @classmethod
    def member_limit(cls, value):
        if value is None:
            return None
        member_limit = int(value)
        if member_limit < 1 or member_limit > cls.MEMBER_LIMIT_MAX:
            raise SpaceErrors.MEMBER_LIMIT_INVALID
        return member_limit

    @classmethod
    def level_names(cls, value):
        if not isinstance(value, list) or len(value) != cls.LEVEL_COUNT:
            raise SpaceErrors.LEVEL_NAMES_INVALID
        normalized = [(item or '').strip() if isinstance(item, str) else '' for item in value]
        if any(not item or len(item) > cls.LEVEL_NAME_MAX_LENGTH for item in normalized):
            raise SpaceErrors.LEVEL_NAMES_INVALID
        if len(set(normalized)) != cls.LEVEL_COUNT:
            raise SpaceErrors.LEVEL_NAMES_INVALID
        return normalized

    @classmethod
    def unverified_group_policy(cls, value):
        normalized = int(value)
        if not cls.UNVERIFIED_GROUP_POLICY_MIN <= normalized <= cls.UNVERIFIED_GROUP_POLICY_MAX:
            raise SpaceErrors.UNVERIFIED_GROUP_JOIN_DISABLED
        return normalized
