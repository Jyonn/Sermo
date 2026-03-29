from django.urls import path

from Space.views import (
    SpaceEmailCodeRequestView,
    SpaceView,
    SpaceLoginView,
    SpaceJoinView,
    SpaceLookupView,
    SpaceMeView,
    SpaceUserListView,
)

urlpatterns = [
    path('email-code', SpaceEmailCodeRequestView.as_view(), name='space email verification code'),
    path('', SpaceView.as_view(), name='space create'),
    path('lookup', SpaceLookupView.as_view(), name='space lookup'),
    path('login', SpaceLoginView.as_view(), name='space email login'),
    path('join', SpaceJoinView.as_view(), name='space join'),
    path('me', SpaceMeView.as_view(), name='space me'),
    path('users', SpaceUserListView.as_view(), name='space users'),
    path('users/online', SpaceUserListView.as_view(force_online=True), name='space online users'),
]
