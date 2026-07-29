import requests

from TravelMap.validators import TravelMapErrors
from utils.global_settings import Globals


def _component(components, *keys):
    for key in keys:
        value = str(components.get(key) or '').strip()
        if value:
            return value
    return ''


def reverse_geocode_check_in(latitude, longitude):
    if not getattr(Globals, 'OPENCAGE_API_KEY', ''):
        raise TravelMapErrors.GEOCODING_UNAVAILABLE
    try:
        response = requests.get(
            Globals.OPENCAGE_GEOCODING_URL,
            params={
                'key': Globals.OPENCAGE_API_KEY,
                'q': f'{latitude},{longitude}',
                'language': 'zh-CN',
                'no_annotations': 1,
                'limit': 1,
            },
            timeout=12,
        )
        response.raise_for_status()
        results = response.json().get('results') or []
        components = (results[0] if results else {}).get('components') or {}
        country_code = _component(components, 'ISO_3166-1_alpha-3').upper()
        country_name = _component(components, 'country')
        region_iso = _component(components, 'ISO_3166-2')
        region_name = _component(
            components,
            'state',
            'province',
            'region',
            'state_district',
            'county',
        )
        if len(country_code) != 3 or not country_name:
            raise ValueError('country is missing')
        if not region_name:
            region_name = country_name
        source_code = region_iso or region_name
        return dict(
            region_code=f'{country_code}:{source_code}',
            region_name=region_name,
            country_code=country_code,
            country_name=country_name,
            provider='opencage',
        )
    except (requests.RequestException, TypeError, ValueError):
        raise TravelMapErrors.GEOCODING_UNAVAILABLE
