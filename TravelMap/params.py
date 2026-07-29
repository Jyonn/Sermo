from smartdjango import Params, Validator

from User.models import User


class TravelMapParams(metaclass=Params):
    user_id = Validator('user_id', final_name='target_user').to(int).to(User.index)
    region_code = Validator('region_code').to(str)
    region_name = Validator('region_name').to(str)
    country_name = Validator('country_name').to(str)
    checked = Validator('checked').to(int).bool(lambda value: value in (0, 1))
    country_code = Validator('country_code').to(str).bool(
        lambda value: len(value.strip()) == 3 and value.strip().isalpha()
    )
