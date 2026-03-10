from django.urls import path

from User.views import SpaceView, SpaceJoinView, SpaceMeView

urlpatterns = [
    path('', SpaceView.as_view(), name='space create'),
    path('join', SpaceJoinView.as_view(), name='space join'),
    path('me', SpaceMeView.as_view(), name='space me'),
]
