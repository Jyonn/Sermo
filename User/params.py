from User.models import BaseUser, HostUser, GuestUser
from utils import processor
from utils.validation.params import Params
from utils.validation.validator import Validator


class BaseUserParams(metaclass=Params):
    model_class = BaseUser

    name: Validator
    lower_name: Validator

    offline_notification_interval: Validator
    notification_choice: Validator

    is_online: Validator
    last_heartbeat: Validator

    email: Validator
    phone: Validator
    bark: Validator

    password: Validator

    user_id: Validator


class HostUserParams(BaseUserParams):
    model_class = HostUser


class GuestUserParams(BaseUserParams):
    model_class = GuestUser

    host_id: Validator


HostUserParams.user_id = Validator('user_id', final_name='user').to(processor.int).to(HostUser.index)
GuestUserParams.user_id = Validator('user_id', final_name='user').to(processor.int).to(GuestUser.index)
GuestUserParams.host_id = Validator('host_id', final_name='host').to(processor.int).to(HostUser.index)
