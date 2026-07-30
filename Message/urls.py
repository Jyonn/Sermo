from django.urls import path

from Message.views import MessageView, MessageBatchView, MessageSyncView, MessageUploadView, MessageBlobView, MessageBlobThumbnailView, MessageImageMetadataView, MessageVideoMetadataView, MessageLinkPreviewView, PinnedMessageView

urlpatterns = [
    path('blob/<slug:blob_slug>/thumbnail', MessageBlobThumbnailView.as_view(), name='message blob thumbnail'),
    path('blob/<slug:blob_slug>', MessageBlobView.as_view(), name='message blob'),
    path('link-preview', MessageLinkPreviewView.as_view(), name='message link preview'),
    path('upload', MessageUploadView.as_view(), name='message upload'),
    path('image-metadata', MessageImageMetadataView.as_view(), name='message image metadata'),
    path('video-metadata', MessageVideoMetadataView.as_view(), name='message video metadata'),
    path('sync', MessageSyncView.as_view(), name='message sync'),
    path('pins', PinnedMessageView.as_view(), name='pinned messages'),
    path('batch', MessageBatchView.as_view(), name='message batch'),
    path('', MessageView.as_view(), name='message'),
]
