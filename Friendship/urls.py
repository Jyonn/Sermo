from django.urls import path

from Friendship.views import (
    FriendshipListView,
    FriendshipStatusView,
    FriendshipRequestView,
    FriendshipRequestRespondView,
    FriendshipRemoveView,
    FriendshipInviteTokenView,
    FriendshipInvitePreviewView,
    FriendshipInviteRedeemView,
    FriendshipExactSearchView,
    FriendshipOperatorView,
)

urlpatterns = [
    path('', FriendshipListView.as_view(), name='friends'),
    path('status', FriendshipStatusView.as_view(), name='friend status'),
    path('requests', FriendshipRequestView.as_view(), name='friend requests'),
    path('search', FriendshipExactSearchView.as_view(), name='friend exact search'),
    path('operators', FriendshipOperatorView.as_view(), name='space operator friends'),
    path('requests/respond', FriendshipRequestRespondView.as_view(), name='friend request respond'),
    path('requests/remove', FriendshipRemoveView.as_view(), name='friend remove'),
    path('invites/token', FriendshipInviteTokenView.as_view(), name='friend invite token'),
    path('invites/preview', FriendshipInvitePreviewView.as_view(), name='friend invite preview'),
    path('invites/redeem', FriendshipInviteRedeemView.as_view(), name='friend invite redeem'),
]
