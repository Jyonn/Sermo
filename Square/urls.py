from django.urls import path

from Square.views import AdminStatementView, PinnedStatementView, SquareQuotaView, SquareStatusView, StatementCommentDetailView, StatementCommentLikeView, StatementCommentView, StatementDetailView, StatementLikeView, StatementLocationView, StatementMediaThumbnailView, StatementMediaView, StatementPinView, StatementUploadView, StatementView


urlpatterns = [
    path('quota', SquareQuotaView.as_view(), name='square quota'),
    path('status', SquareStatusView.as_view(), name='square status'),
    path('media/<slug:blob_slug>/thumbnail', StatementMediaThumbnailView.as_view(), name='square media thumbnail'),
    path('media/<slug:blob_slug>', StatementMediaView.as_view(), name='square media'),
    path('upload', StatementUploadView.as_view(), name='square upload'),
    path('location', StatementLocationView.as_view(), name='square location'),
    path('statements', StatementView.as_view(), name='square statements'),
    path('admin/statements', AdminStatementView.as_view(), name='square admin statements'),
    path('statements/pinned', PinnedStatementView.as_view(), name='square pinned statement'),
    path('statements/<int:statement_id>/pin', StatementPinView.as_view(), name='square statement pin'),
    path('statements/<int:statement_id>/comments', StatementCommentView.as_view(), name='square statement comments'),
    path('statements/<int:statement_id>', StatementDetailView.as_view(), name='square statement detail'),
    path('statements/<int:statement_id>/like', StatementLikeView.as_view(), name='square statement like'),
    path('comments/<int:comment_id>/like', StatementCommentLikeView.as_view(), name='square comment like'),
    path('comments/<int:comment_id>', StatementCommentDetailView.as_view(), name='square comment detail'),
]
