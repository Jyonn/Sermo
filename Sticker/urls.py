from django.urls import path

from Sticker.views import StickerAssetView, StickerCompleteView, StickerPrepareView, StickerView


urlpatterns = [
    path('assets/<int:asset_id>', StickerAssetView.as_view(), name='sticker asset'),
    path('prepare', StickerPrepareView.as_view(), name='sticker prepare'),
    path('complete', StickerCompleteView.as_view(), name='sticker complete'),
    path('', StickerView.as_view(), name='stickers'),
]
