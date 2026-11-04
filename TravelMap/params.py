from smartdjango import Params, Validator

from User.models import User
from Chat.models import Chat


class TravelMapParams(metaclass=Params):
    history_chat = Validator('chat_id',final_name='history_chat').to(int).to(Chat.index).null().default(None)
    history_user = Validator('user_id',final_name='history_user').to(int).to(User.index).null().default(None)
    selected_ids = Validator('selected_ids').to(str).bool(lambda value: len(value)<=4000 and all(part.isdigit() and 0<int(part)<=2**53-1 for part in value.split(',') if part)).default('')
    user_id = Validator('user_id', final_name='target_user').to(int).to(User.index)
    chat_id = Validator('chat_id', final_name='chat').to(int).to(Chat.index)
    latitude = Validator('latitude').to(float).bool(lambda value: -90 <= value <= 90)
    longitude = Validator('longitude').to(float).bool(lambda value: -180 <= value <= 180)
    accuracy_meters = Validator('accuracy_meters').to(float).bool(lambda value: 0 <= value <= 50000)
    region_code = Validator('region_code').to(str).bool(
        lambda value: 2 <= len(value.strip()) <= 80
    )
    region_name = Validator('region_name').to(str).bool(
        lambda value: 1 <= len(value.strip()) <= 120
    )
    country_code = Validator('country_code').to(str).bool(
        lambda value: len(value.strip()) == 3 and value.strip().isalpha()
    )
    country_name = Validator('country_name').to(str).bool(
        lambda value: 1 <= len(value.strip()) <= 120
    )
