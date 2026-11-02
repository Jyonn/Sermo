from django.db.models import OuterRef, Subquery
from Sticker.models import StickerAsset, UserSticker, UserStickerUsage
from User.models import User


def sticker_library_payload(user, offset, limit, request=None, series_id=None):
    query = UserSticker.objects.filter(user=user)
    if series_id is None:
        query=query.filter(in_home=True)
    elif series_id==0:
        query=query.filter(is_uploaded=True)
    else:
        from Sticker.models import StickerSeries
        group=StickerSeries.objects.filter(id=series_id,user=user).first()
        query=query.filter(asset_id__in=group.draft_assets if group else [],is_uploaded=True)
    page = list(query.select_related('asset','series').prefetch_related('asset__managed_resources')[offset:offset + limit + 1])
    rows = page[:limit]
    return dict(items=[row.jsonl(request=request) for row in rows], has_more=len(page) > limit, next_offset=offset + len(rows))


def sticker_explore_payload(user, offset, limit, request=None):
    owned_asset_ids = UserSticker.objects.filter(user=user,in_home=True).values('asset_id')
    source_user_id = UserSticker.objects.filter(asset_id=OuterRef('pk')).order_by(
        'created_at', 'id',
    ).values('user_id')[:1]
    asset_queryset = StickerAsset.objects.filter(managed_resources__isnull=True,owners__in_home=True).distinct().exclude(id__in=owned_asset_ids).annotate(
        source_user_id=Subquery(source_user_id),
    )
    page = list(asset_queryset.order_by('-created_at', '-id')[offset:offset + limit + 1])
    assets = page[:limit]
    frequent_ids = UserStickerUsage.top_asset_ids_for_user(user, limit=5) if offset == 0 else []
    frequent_by_id = {
        asset.id: asset
        for asset in asset_queryset.filter(id__in=frequent_ids)
    }
    frequent_assets = [frequent_by_id[asset_id] for asset_id in frequent_ids if asset_id in frequent_by_id]
    source_users = User.objects.in_bulk({
        asset.source_user_id
        for asset in [*assets, *frequent_assets]
        if asset.source_user_id
    })
    serialize = lambda asset: asset.jsonl(
        request=request,
        source_user=source_users.get(asset.source_user_id),
    )
    return dict(
        items=[serialize(asset) for asset in assets],
        frequent_items=[serialize(asset) for asset in frequent_assets],
        has_more=len(page) > limit,
        next_offset=offset + len(assets),
    )
