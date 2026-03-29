from django.urls import path

from Space.views import (
    SpaceAdminSettingsView,
    SpaceAdminDashboardView,
    SpaceAdminOfficialLoginTicketView,
    SpaceAdminUserListView,
    SpaceAdminUserRemoveView,
    SpaceEmailCodeRequestView,
    SpaceOfficialLoginExchangeView,
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
    path('admin/dashboard', SpaceAdminDashboardView.as_view(), name='space admin dashboard'),
    path('admin/official-login-ticket', SpaceAdminOfficialLoginTicketView.as_view(), name='space admin official login ticket'),
    path('admin/settings', SpaceAdminSettingsView.as_view(), name='space admin settings'),
    path('admin/users', SpaceAdminUserListView.as_view(), name='space admin users'),
    path('admin/users/remove', SpaceAdminUserRemoveView.as_view(), name='space admin user remove'),
    path('lookup', SpaceLookupView.as_view(), name='space lookup'),
    path('login', SpaceLoginView.as_view(), name='space email login'),
    path('official-login/exchange', SpaceOfficialLoginExchangeView.as_view(), name='space official login exchange'),
    path('join', SpaceJoinView.as_view(), name='space join'),
    path('me', SpaceMeView.as_view(), name='space me'),
    path('users', SpaceUserListView.as_view(), name='space users'),
    path('users/online', SpaceUserListView.as_view(force_online=True), name='space online users'),
]
