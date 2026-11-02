import json
from django.db import transaction, IntegrityError
from django.db.models import Count
from django.views import View
from smartdjango import OK
from Sticker.models import StickerSeries, UserSticker
from Sticker.validators import StickerErrors
from utils import auth


def series_name(value):
    name = str(value or '').strip()
    if not name or sum(1 if ord(c) < 128 else 2 for c in name) > 10 or any(not(c.isalnum() or c in ' -_') for c in name):
        raise StickerErrors.INVALID_HASH
    return name


def data(request):
    try:
        value = json.loads(request.body)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise StickerErrors.INVALID_HASH


class StickerSeriesView(View):
    @auth.require_user
    def get(self, request):
        return [dict(series_id=row.id, name=row.name, count=row.count) for row in
                StickerSeries.objects.filter(user=request.user).annotate(count=Count('stickers'))]

    @auth.require_user
    def post(self, request):
        name = series_name(data(request).get('name'))
        if StickerSeries.objects.filter(user=request.user).count() >= 50:
            raise StickerErrors.INVALID_HASH
        row, _ = StickerSeries.objects.get_or_create(user=request.user, name=name)
        return dict(series_id=row.id, name=row.name, count=row.stickers.count())


class StickerSeriesDetailView(View):
    @auth.require_user
    def post(self, request, series_id):
        row = StickerSeries.objects.filter(id=series_id, user=request.user).first()
        if not row:
            raise StickerErrors.NOT_FOUND
        row.name = series_name(data(request).get('name'))
        try:
            with transaction.atomic():
                row.save(update_fields=['name'])
        except IntegrityError:
            raise StickerErrors.INVALID_HASH
        return dict(series_id=row.id, name=row.name, count=row.stickers.count())

    @auth.require_user
    def delete(self, request, series_id):
        row = StickerSeries.objects.filter(id=series_id, user=request.user).first()
        if not row:
            raise StickerErrors.NOT_FOUND
        row.delete()  # SET_NULL preserves the user's stickers.
        return OK


class StickerSeriesAssignView(View):
    @auth.require_user
    def post(self, request):
        value = data(request)
        ids, series_id = value.get('sticker_ids'), value.get('series_id')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(type(i) is not int for i in ids):
            raise StickerErrors.INVALID_HASH
        with transaction.atomic():
            if series_id is not None and (type(series_id) is not int or not StickerSeries.objects.select_for_update().filter(id=series_id, user=request.user).exists()):
                raise StickerErrors.NOT_ACCESSIBLE
            rows = UserSticker.objects.select_for_update().filter(id__in=set(ids), user=request.user)
            if rows.count() != len(set(ids)):
                raise StickerErrors.NOT_ACCESSIBLE
            rows.update(series_id=series_id)
        return OK
