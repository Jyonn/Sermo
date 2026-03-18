from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator

from Friendship.models import Friendship
from User.models import User


class FriendshipParams(metaclass=Params):
    model_class = Friendship

    request_id = Validator('request_id', final_name='friendship') \
        .to(int) \
        .to(Friendship.index)

    to_user_id = Validator('to_user_id', final_name='to_user') \
        .to(int) \
        .to(User.index)

    accept = Validator('accept') \
        .to(int) \
        .bool(lambda x: x in (0, 1), message=_('accept should be 0 or 1'))

    token = Validator('token') \
        .to(str) \
        .bool(lambda x: len(x.strip()) > 0, message=_('token is required'))
