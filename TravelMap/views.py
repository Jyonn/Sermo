import json
from django.db import transaction
from django.views import View
from smartdjango import OK, analyse

from TravelMap.models import MapAccessGrant, MapChatGrant, MapCheckIn, TravelMap
from TravelMap.params import TravelMapParams
from TravelMap.validators import TravelMapErrors
from TravelMap.unlocks import unlocked_city_bubble_styles
from Message.models import Message, MessageTypeChoice
from User.models import NotificationEvent
from utils import auth
from utils.auth import Request


class MyTravelMapView(View):
    @auth.require_user
    def get(self, request: Request):
        regions = MapCheckIn.objects.filter(user=request.user)
        return dict(
            owner=request.user.tiny_json(),
            regions=[item.json() for item in regions],
        )

class TravelMapCheckInView(View):
    MAX_ACCURACY_METERS = 5000

    @auth.require_user
    @analyse.json(
        TravelMapParams.latitude,
        TravelMapParams.longitude,
        TravelMapParams.accuracy_meters,
        TravelMapParams.region_code,
        TravelMapParams.region_name,
        TravelMapParams.country_code,
        TravelMapParams.country_name,
    )
    def post(self, request: Request):
        if request.json.accuracy_meters > self.MAX_ACCURACY_METERS:
            raise TravelMapErrors.LOCATION_TOO_INACCURATE
        checked = MapCheckIn.check_in(
            request.user,
            request.json.region_code,
            request.json.region_name,
            request.json.country_code,
            request.json.country_name,
            request.json.latitude,
            request.json.longitude,
            request.json.accuracy_meters,
            'frontend-boundary',
        )
        regions = MapCheckIn.objects.filter(user=request.user)
        return dict(
            owner=request.user.tiny_json(),
            regions=[item.json() for item in regions],
            checked_region=checked.json(),
            city_bubble_styles=unlocked_city_bubble_styles(request.user),
        )


class UserTravelMapView(View):
    @auth.require_user
    @analyse.query(TravelMapParams.user_id)
    def get(self, request: Request):
        return TravelMap.comparison_payload(request.user, request.query.target_user)


class MapAccessView(View):
    @auth.require_user
    @analyse.query(TravelMapParams.user_id)
    def delete(self, request: Request):
        MapAccessGrant.revoke(request.user, request.query.target_user)
        return OK


class MapAccessReciprocateView(View):
    @auth.require_user
    @analyse.query(TravelMapParams.user_id)
    def post(self, request: Request):
        MapAccessGrant.reciprocate(request.user, request.query.target_user)
        return MapAccessGrant.status_between(request.user, request.query.target_user)


class MapAccessOverviewView(View):
    @auth.require_user
    def get(self, request: Request):
        return MapChatGrant.access_overview(request.user)


class ChatMapAccessView(View):
    @auth.require_user
    @analyse.query(TravelMapParams.chat_id)
    def get(self, request: Request):
        return MapChatGrant.status(request.query.chat, request.user)

    @auth.require_user
    @analyse.query(TravelMapParams.chat_id)
    def post(self, request: Request):
        with transaction.atomic():
            grant = MapChatGrant.grant(request.query.chat, request.user)
            status = MapChatGrant.status(request.query.chat, request.user)
            if grant._was_activated:
                message = Message.create(
                    request.query.chat,
                    request.user,
                    MessageTypeChoice.MAP_ACCESS,
                    json.dumps({
                    'kind': 'map_access',
                    'chat_grant': True,
                    'message_key': 'travel_map_join',
                }, ensure_ascii=False),
                )
                NotificationEvent.emit_message_notifications(message, actor=request.user)
                status['invitation_message'] = message.jsonl(request=request)
        return status

    @auth.require_user
    @analyse.query(TravelMapParams.chat_id)
    def delete(self, request: Request):
        MapChatGrant.revoke(request.query.chat, request.user)
        return MapChatGrant.status(request.query.chat, request.user)


class ChatTravelMapsView(View):
    @auth.require_user
    @analyse.query(TravelMapParams.chat_id)
    def get(self, request: Request):
        return MapChatGrant.maps(request.query.chat, request.user)
