from django.urls import path

from Friendship.views import (
    FriendshipListView,
    FriendshipRequestView,
    FriendshipRequestRespondView,
    FriendshipRemoveView,
    FriendshipInviteTokenView,
    FriendshipInviteRedeemView,
)

urlpatterns = [
    path('', FriendshipListView.as_view(), name='friends'),
    path('requests', FriendshipRequestView.as_view(), name='friend requests'),
    path('requests/respond', FriendshipRequestRespondView.as_view(), name='friend request respond'),
    path('requests/remove', FriendshipRemoveView.as_view(), name='friend remove'),
    path('invites/token', FriendshipInviteTokenView.as_view(), name='friend invite token'),
    path('invites/redeem', FriendshipInviteRedeemView.as_view(), name='friend invite redeem'),
]
