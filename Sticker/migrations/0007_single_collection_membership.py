from django.db import migrations


def reserve_memberships(apps,schema_editor):
    Series=apps.get_model('Sticker','StickerSeries')
    Sticker=apps.get_model('Sticker','UserSticker')
    reservations={}
    rows=list(Series.objects.order_by('created_at','id'))
    # Review history is immutable; a deployment must never silently rewrite it.
    for row in rows:
        ids=set(row.draft_assets)
        for snapshot in (row.published_snapshot,row.pending_snapshot):
            ids.update((snapshot or {}).get('asset_ids',[]))
        for asset_id in ids:
            key=(row.user_id,asset_id)
            if key in reservations and reservations[key]!=row.id:
                raise RuntimeError('Existing collections have overlapping assets; resolve their membership before deployment.')
            reservations[key]=row.id
    for (user_id,asset_id),series_id in reservations.items():
        Sticker.objects.filter(user_id=user_id,asset_id=asset_id,is_uploaded=True).update(series_id=series_id)


class Migration(migrations.Migration):
    dependencies=[('Sticker','0006_stickerseries_draft_assets_and_more')]
    operations=[migrations.RunPython(reserve_memberships,migrations.RunPython.noop)]
