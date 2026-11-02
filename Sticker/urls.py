from django.urls import path
from Sticker.series import StickerSeriesView, StickerSeriesDetailView, StickerSeriesAssignView

from Sticker.views import (
    StickerAssetView,
    StickerCollectView,
    StickerCompleteView,
    StickerExploreView,
    StickerPrepareView,
    StickerView,
)


urlpatterns = [
    path('series', StickerSeriesView.as_view()),
    path('series/assign', StickerSeriesAssignView.as_view()),
    path('series/<int:series_id>', StickerSeriesDetailView.as_view()),
    path('assets/<int:asset_id>', StickerAssetView.as_view(), name='sticker asset'),
    path('explore', StickerExploreView.as_view(), name='sticker explore'),
    path('collect', StickerCollectView.as_view(), name='sticker collect'),
    path('prepare', StickerPrepareView.as_view(), name='sticker prepare'),
    path('complete', StickerCompleteView.as_view(), name='sticker complete'),
    path('', StickerView.as_view(), name='stickers'),
]
