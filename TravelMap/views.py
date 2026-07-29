import requests
from django.core.cache import cache
from django.views import View
from smartdjango import OK, analyse

from TravelMap.models import MapAccessGrant, MapCheckIn, TravelMap
from TravelMap.params import TravelMapParams
from TravelMap.validators import TravelMapErrors
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

    @auth.require_user
    @analyse.json(
        TravelMapParams.region_code,
        TravelMapParams.region_name,
        TravelMapParams.country_code,
        TravelMapParams.country_name,
        TravelMapParams.checked,
    )
    def post(self, request: Request):
        MapCheckIn.set_checked(
            request.user,
            request.json.region_code,
            request.json.region_name,
            request.json.country_code,
            request.json.country_name,
            bool(request.json.checked),
        )
        regions = MapCheckIn.objects.filter(user=request.user)
        return dict(
            owner=request.user.tiny_json(),
            regions=[item.json() for item in regions],
        )


class UserTravelMapView(View):
    @auth.require_user
    @analyse.query(TravelMapParams.user_id)
    def get(self, request: Request):
        return TravelMap.comparison_payload(request.user, request.query.target_user)


class MapAccessView(View):
    @auth.require_user
    @analyse.query(TravelMapParams.user_id)
    def get(self, request: Request):
        return MapAccessGrant.status_between(request.user, request.query.target_user)

    @auth.require_user
    @analyse.query(TravelMapParams.user_id)
    def post(self, request: Request):
        MapAccessGrant.grant(request.user, request.query.target_user)
        return MapAccessGrant.status_between(request.user, request.query.target_user)

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


class MapGeometryView(View):
    CACHE_SECONDS = 60 * 60 * 24 * 14

    @auth.require_user
    @analyse.query(TravelMapParams.country_code)
    def get(self, request: Request):
        country_code = request.query.country_code.strip().upper()
        cache_key = f'travel-map:adm1:{country_code}'
        cached = cache.get(cache_key)
        if cached:
            return cached
        try:
            metadata_response = requests.get(
                f'https://www.geoboundaries.org/api/current/gbOpen/{country_code}/ADM1/',
                timeout=12,
            )
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            geometry_url = metadata.get('simplifiedGeometryGeoJSON') or metadata.get('gjDownloadURL')
            if not geometry_url:
                raise ValueError('missing geometry URL')
            geometry_response = requests.get(geometry_url, timeout=25)
            geometry_response.raise_for_status()
            payload = geometry_response.json()
        except (requests.RequestException, ValueError, TypeError):
            raise TravelMapErrors.GEOMETRY_UNAVAILABLE
        cache.set(cache_key, payload, cls.CACHE_SECONDS)
        return payload
