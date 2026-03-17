from django.urls import path

from Chat.views import (
    ChatListView,
    DirectChatView,
    GroupChatView,
    GroupChatNameView,
    GroupChatMemberView,
    GroupChatInviteRespondView,
    GroupChatInviteListView,
    GroupChatLeaveView,
    ChatReadView,
)

urlpatterns = [
    path('', ChatListView.as_view(), name='chat_list'),
    path('direct', DirectChatView.as_view(), name='direct_chat'),
    path('group', GroupChatView.as_view(), name='group_chat'),
    path('group/name', GroupChatNameView.as_view(), name='group_chat_name'),
    path('group/members', GroupChatMemberView.as_view(), name='group_chat_members'),
    path('group/invites', GroupChatInviteListView.as_view(), name='group_chat_invites'),
    path('group/invite/respond', GroupChatInviteRespondView.as_view(), name='group_chat_invite_respond'),
    path('group/leave', GroupChatLeaveView.as_view(), name='group_chat_leave'),
    path('read', ChatReadView.as_view(), name='chat_read'),
]
