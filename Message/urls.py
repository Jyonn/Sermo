from django.urls import path

from Message.views import MessageView, MessageSyncView, MessageUploadView, MessageBlobView, MessageBlobThumbnailView

urlpatterns = [
    path('blob/<slug:blob_slug>/thumbnail', MessageBlobThumbnailView.as_view(), name='message blob thumbnail'),
    path('blob/<slug:blob_slug>', MessageBlobView.as_view(), name='message blob'),
    path('upload', MessageUploadView.as_view(), name='message upload'),
    path('sync', MessageSyncView.as_view(), name='message sync'),
    path('', MessageView.as_view(), name='message'),
]
