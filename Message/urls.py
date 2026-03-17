from django.urls import path

from Message.views import MessageView, MessageSyncView

urlpatterns = [
    path('sync', MessageSyncView.as_view(), name='message sync'),
    path('', MessageView.as_view(), name='message'),
]
