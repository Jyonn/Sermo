from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator

from Space.models import Space, SpaceEmailVerificationCode
from User.params import UserParams


class SpaceParams(metaclass=Params):
    model_class = Space

    slug: Validator
    name: Validator
    email: Validator
    member_limit = Validator('member_limit') \
        .to(int) \
        .null().default(None) \
        .to(Space.vldt.member_limit)
    password = UserParams.password.copy().null().default(None)
    language = UserParams.language.copy()
    group_square_enabled = Validator('group_square_enabled') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('group_square_enabled should be 0 or 1'))


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


class SpaceLookupParams(metaclass=Params):
    slug = SpaceParams.slug.copy()


class SpaceOfficialLoginTicketParams(metaclass=Params):
    token = Validator('token').to(str)
