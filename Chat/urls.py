from django.urls import path

from Chat.views import (
    ChatListView,
    GroupChatView,
    GroupChatNameView,
    GroupChatMemberView,
    GroupChatInviteRespondView,
    ChatReadView,
)

urlpatterns = [
    path('', ChatListView.as_view(), name='chat_list'),
    path('group', GroupChatView.as_view(), name='group_chat'),
    path('group/name', GroupChatNameView.as_view(), name='group_chat_name'),
    path('group/members', GroupChatMemberView.as_view(), name='group_chat_members'),
    path('group/invite/respond', GroupChatInviteRespondView.as_view(), name='group_chat_invite_respond'),
    path('read', ChatReadView.as_view(), name='chat_read'),
]
