from smartdjango import Params, Validator

from User.models import User
from Chat.models import Chat


class TravelMapParams(metaclass=Params):
    user_id = Validator('user_id', final_name='target_user').to(int).to(User.index)
    chat_id = Validator('chat_id', final_name='chat').to(int).to(Chat.index)
    latitude = Validator('latitude').to(float).bool(lambda value: -90 <= value <= 90)
    longitude = Validator('longitude').to(float).bool(lambda value: -180 <= value <= 180)
    accuracy_meters = Validator('accuracy_meters').to(float).bool(lambda value: 0 <= value <= 50000)
    country_code = Validator('country_code').to(str).bool(
        lambda value: len(value.strip()) == 3 and value.strip().isalpha()
    )
