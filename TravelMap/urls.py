from django.urls import path

from TravelMap.views import (
    ChatMapAccessView,
    ChatTravelMapsView,
    MapAccessReciprocateView,
    MapAccessView,
    MapGeometryView,
    MyTravelMapView,
    TravelMapCheckInView,
    UserTravelMapView,
)


urlpatterns = [
    path('me', MyTravelMapView.as_view(), name='my travel map'),
    path('me/check-in', TravelMapCheckInView.as_view(), name='travel map check in'),
    path('users', UserTravelMapView.as_view(), name='user travel map'),
    path('access', MapAccessView.as_view(), name='travel map access'),
    path('access/reciprocate', MapAccessReciprocateView.as_view(), name='travel map access reciprocate'),
    path('geometry', MapGeometryView.as_view(), name='travel map geometry'),
    path('chats/access', ChatMapAccessView.as_view(), name='chat travel map access'),
    path('chats/maps', ChatTravelMapsView.as_view(), name='chat travel maps'),
]
