from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator

from Message.validators import MessageValidator
from Space.models import Space, SpaceEmailVerificationCode
from Space.validators import SpaceValidator
from Message.params import MessageParams
from User.params import UserParams


class SpaceParams(metaclass=Params):
    model_class = Space

    slug: Validator
    name: Validator
    email: Validator
    member_limit: Validator
    level_names = Validator('level_names').null().default(None)
    password = UserParams.password.copy().null().default(None)
    language = UserParams.language.copy()
    group_square_enabled = Validator('group_square_enabled') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('group_square_enabled should be 0 or 1'))
    chat_enabled = Validator('chat_enabled').to(int).null().default(1).bool(
        lambda x: x in (0, 1), message=_('chat_enabled should be 0 or 1'))
    square_explore_enabled = Validator('square_explore_enabled').to(int).null().default(1).bool(
        lambda x: x in (0, 1), message=_('square_explore_enabled should be 0 or 1'))
    unverified_group_policy = Validator('unverified_group_policy').to(int).null().default(2).to(SpaceValidator.unverified_group_policy)


class SpaceEmailVerificationCodeParams(metaclass=Params):
    model_class = SpaceEmailVerificationCode

    email = SpaceParams.email.copy().null().default(None)
    code: Validator
    slug = SpaceParams.slug.copy().null().default(None)


class SpaceUserListParams(metaclass=Params):
    q = Validator('q').to(str).null().default(None)
    online = Validator('online') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: True if x is None else x in (0, 1), message=_('online should be 0 or 1'))
    limit = Validator('limit') \
        .to(int) \
        .null().default(50) \
        .bool(lambda x: 1 <= x <= 200, message=_('limit should be between 1 and 200'))
    offset = Validator('offset') \
        .to(int) \
        .null().default(0) \
        .bool(lambda x: x >= 0, message=_('offset should be greater than or equal to 0'))


class SpaceOperatorParams(metaclass=Params):
    user_id = Validator('user_id').to(int)


class SpaceLookupParams(metaclass=Params):
    slug = SpaceParams.slug.copy()


class SpaceOfficialLoginTicketParams(metaclass=Params):
    token = Validator('token').to(str)


class SpacePhoneVerificationParams(metaclass=Params):
    phone = Validator('phone').to(str).to(lambda value: value.strip())
    code = Validator('code').to(str).to(lambda value: value.strip())


class SpaceIdentityParams(metaclass=Params):
    file_name = Validator('file_name').to(str)
    content_type = Validator('content_type').to(str)
    key = Validator('key').to(str)


class SpaceAdminBroadcastParams(metaclass=Params):
    content = MessageParams.content.copy()
    type = MessageParams.type.copy().null().default(0)
    broadcast_id = Validator('broadcast_id') \
        .to(str) \
        .to(lambda x: x.strip()) \
        .bool(
            lambda x: 0 < len(x) <= MessageValidator.MAX_CLIENT_MESSAGE_ID_LENGTH,
            message=_('Invalid broadcast id'),
        )
