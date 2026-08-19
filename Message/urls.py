from django.urls import path

from Message.views import MessageView, MessageBatchView, MessageClearView, MessageEventSyncView, MessageReconcileView, MessageSearchView, MessageUploadView, MessageBlobView, MessageBlobThumbnailView, MessageMediaMetadataView, MessageLinkPreviewView, PinnedMessageView

urlpatterns = [
    path('blob/<slug:blob_slug>/thumbnail', MessageBlobThumbnailView.as_view(), name='message blob thumbnail'),
    path('blob/<slug:blob_slug>', MessageBlobView.as_view(), name='message blob'),
    path('link-preview', MessageLinkPreviewView.as_view(), name='message link preview'),
    path('upload', MessageUploadView.as_view(), name='message upload'),
    path('media-metadata', MessageMediaMetadataView.as_view(), name='message media metadata'),
    path('sync-v2', MessageEventSyncView.as_view(), name='message event sync'),
    path('pins', PinnedMessageView.as_view(), name='pinned messages'),
    path('batch', MessageBatchView.as_view(), name='message batch'),
    path('clear', MessageClearView.as_view(), name='message clear'),
    path('reconcile', MessageReconcileView.as_view(), name='message reconcile'),
    path('search', MessageSearchView.as_view(), name='message search'),
    path('', MessageView.as_view(), name='message'),
]
