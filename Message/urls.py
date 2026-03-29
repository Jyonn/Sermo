from django.urls import path

from Message.views import MessageView, MessageSyncView, MessageUploadView

urlpatterns = [
    path('upload', MessageUploadView.as_view(), name='message upload'),
    path('sync', MessageSyncView.as_view(), name='message sync'),
    path('', MessageView.as_view(), name='message'),
]
