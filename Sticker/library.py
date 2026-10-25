from django.db.models import OuterRef, Subquery
from Sticker.models import StickerAsset, UserSticker, UserStickerUsage
from User.models import User


def sticker_library_payload(user, offset, limit, request=None):
    page = list(UserSticker.objects.filter(user=user).select_related('asset')[offset:offset + limit + 1])
    rows = page[:limit]
    return dict(items=[row.jsonl(request=request) for row in rows], has_more=len(page) > limit, next_offset=offset + len(rows))


def sticker_explore_payload(user, offset, limit, request=None):
    owned_asset_ids = UserSticker.objects.filter(user=user).values('asset_id')
    source_user_id = UserSticker.objects.filter(asset_id=OuterRef('pk')).order_by(
        'created_at', 'id',
    ).values('user_id')[:1]
    asset_queryset = StickerAsset.objects.exclude(id__in=owned_asset_ids).annotate(
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
