from django.urls import path

from Friendship.views import (
    FriendshipListView,
    FriendshipRequestView,
    FriendshipRequestRespondView,
    FriendshipRemoveView,
)

urlpatterns = [
    path('', FriendshipListView.as_view(), name='friends'),
    path('requests', FriendshipRequestView.as_view(), name='friend requests'),
    path('requests/respond', FriendshipRequestRespondView.as_view(), name='friend request respond'),
    path('requests/remove', FriendshipRemoveView.as_view(), name='friend remove'),
]
