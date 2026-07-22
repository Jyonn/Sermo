from django.urls import path

from Message.views import MessageView, MessageSyncView, MessageUploadView, MessageBlobView, MessageBlobThumbnailView, MessageImageMetadataView, MessageLinkPreviewView

urlpatterns = [
    path('blob/<slug:blob_slug>/thumbnail', MessageBlobThumbnailView.as_view(), name='message blob thumbnail'),
    path('blob/<slug:blob_slug>', MessageBlobView.as_view(), name='message blob'),
    path('link-preview', MessageLinkPreviewView.as_view(), name='message link preview'),
    path('upload', MessageUploadView.as_view(), name='message upload'),
    path('image-metadata', MessageImageMetadataView.as_view(), name='message image metadata'),
    path('sync', MessageSyncView.as_view(), name='message sync'),
    path('', MessageView.as_view(), name='message'),
]
