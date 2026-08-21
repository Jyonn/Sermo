from django.urls import path

from Message.views import MessageView, MessageBatchView, MessageClearView, MessageEventSyncView, MessageForwardView, MessageHistoryRecoveryView, MessageReconcileView, MessageSearchView, MessageUploadView, MessageBlobView, MessageBlobThumbnailView, MessageMediaMetadataView, MessageLinkPreviewView, MessageResourceFinalizeView, MessageResourceView, PinnedMessageView

urlpatterns = [
    path('blob/<slug:blob_slug>/thumbnail', MessageBlobThumbnailView.as_view(), name='message blob thumbnail'),
    path('blob/<slug:blob_slug>', MessageBlobView.as_view(), name='message blob'),
    path('link-preview', MessageLinkPreviewView.as_view(), name='message link preview'),
    path('upload', MessageUploadView.as_view(), name='message upload'),
    path('resources', MessageResourceView.as_view(), name='message resources'),
    path('resources/finalize', MessageResourceFinalizeView.as_view(), name='message resource finalize'),
    path('media-metadata', MessageMediaMetadataView.as_view(), name='message media metadata'),
    path('sync-v2', MessageEventSyncView.as_view(), name='message event sync'),
    path('pins', PinnedMessageView.as_view(), name='pinned messages'),
    path('batch', MessageBatchView.as_view(), name='message batch'),
    path('forward', MessageForwardView.as_view(), name='message forward'),
    path('clear', MessageClearView.as_view(), name='message clear'),
    path('restore', MessageHistoryRecoveryView.as_view(), name='message history restore'),
    path('reconcile', MessageReconcileView.as_view(), name='message reconcile'),
    path('search', MessageSearchView.as_view(), name='message search'),
    path('', MessageView.as_view(), name='message'),
]
