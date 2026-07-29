from django.urls import path

from TravelMap.views import MapAccessReciprocateView, MapAccessView, MapGeometryView, MyTravelMapView, UserTravelMapView


urlpatterns = [
    path('me', MyTravelMapView.as_view(), name='my travel map'),
    path('users', UserTravelMapView.as_view(), name='user travel map'),
    path('access', MapAccessView.as_view(), name='travel map access'),
    path('access/reciprocate', MapAccessReciprocateView.as_view(), name='travel map access reciprocate'),
    path('geometry', MapGeometryView.as_view(), name='travel map geometry'),
]
