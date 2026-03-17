from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator

from Space.models import Space, SpaceEmailVerificationCode
from User.params import UserParams


class SpaceParams(metaclass=Params):
    model_class = Space

    slug: Validator
    name: Validator
    email: Validator
    password = UserParams.password.copy().null().default(None)
    language = UserParams.language.copy()


class SpaceEmailVerificationCodeParams(metaclass=Params):
    model_class = SpaceEmailVerificationCode

    email: Validator
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
