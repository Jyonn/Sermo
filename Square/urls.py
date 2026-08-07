from django.urls import path

from Square.views import StatementMediaThumbnailView, StatementMediaView, StatementUploadView, StatementView


urlpatterns = [
    path('media/<slug:blob_slug>/thumbnail', StatementMediaThumbnailView.as_view(), name='square media thumbnail'),
    path('media/<slug:blob_slug>', StatementMediaView.as_view(), name='square media'),
    path('upload', StatementUploadView.as_view(), name='square upload'),
    path('statements', StatementView.as_view(), name='square statements'),
]
