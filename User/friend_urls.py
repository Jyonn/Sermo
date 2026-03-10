from django.urls import path

from User.views import FriendListView, FriendRequestView, FriendRequestRespondView

urlpatterns = [
    path('', FriendListView.as_view(), name='friends'),
    path('requests', FriendRequestView.as_view(), name='friend requests'),
    path('requests/respond', FriendRequestRespondView.as_view(), name='friend request respond'),
]
