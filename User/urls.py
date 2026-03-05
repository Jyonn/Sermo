from django.urls import path

from User.views import (
    HostView,
    GuestView,
    HeartbeatView,
    RefreshView,
    LogoutView,
    SubdomainView,
    GuestNicknameView,
    HostGuestListView,
    GuestDeleteView,
)

urlpatterns = [
    path('host', HostView.as_view(), name='host login'),
    path('guest', GuestView.as_view(), name='guest login'),
    path('guest/delete', GuestDeleteView.as_view(), name='guest delete'),
    path('guest/nickname', GuestNicknameView.as_view(), name='guest nickname'),
    path('host/guests', HostGuestListView.as_view(), name='host guest list'),
    path('heartbeat', HeartbeatView.as_view(), name='heartbeat'),
    path('refresh', RefreshView.as_view(), name='token refresh'),
    path('logout', LogoutView.as_view(), name='token revoke'),
    path('host/subdomain', SubdomainView.as_view(), name='host subdomain'),
]
