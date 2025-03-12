from django.urls import path

from Chat.views import ChatListView, GroupChatView, GroupChatNameView

urlpatterns = [
    path('', ChatListView.as_view(), name='chat_list'),
    path('group', GroupChatView.as_view(), name='group_chat'),
    path('group/name', GroupChatNameView.as_view(), name='group_chat_name'),
]
