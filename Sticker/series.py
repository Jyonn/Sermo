import json
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.views import View
from smartdjango import OK
from Sticker.models import StickerSeries, UserSticker, StickerAsset
from Sticker.validators import StickerErrors
from utils import auth


def series_name(value):
    name=str(value or '').strip()
    if not name or sum(1 if ord(c)<128 else 2 for c in name)>10 or any(not(c.isalnum() or c in ' -_') for c in name):
        raise StickerErrors.INVALID_HASH
    return name


def data(request):
    try:
        value=json.loads(request.body)
        if not isinstance(value,dict):raise ValueError
        return value
    except (ValueError,TypeError):raise StickerErrors.INVALID_HASH


def owned(request,series_id,editable=True):
    row=StickerSeries.objects.select_for_update().filter(id=series_id,user=request.user).first()
    if not row:raise StickerErrors.NOT_FOUND
    if editable and row.pending_snapshot is not None:raise StickerErrors.NOT_ACCESSIBLE
    return row


def claim_membership(group, rows):
    if any(row.series_id not in (None, group.id) for row in rows):
        raise StickerErrors.SERIES_CONFLICT
    UserSticker.objects.filter(id__in=[row.id for row in rows]).update(series=group)
    for row in rows:row.series_id=group.id


def release_unused_membership(group):
    reserved=set(group.draft_assets)
    for snapshot in (group.published_snapshot, group.pending_snapshot):
        reserved.update((snapshot or {}).get('asset_ids', []))
    UserSticker.objects.filter(user=group.user,series=group).exclude(asset_id__in=reserved).update(series=None)


def payload(row,request=None,admin=False):
    value=dict(series_id=row.id,name=row.name,count=len(row.draft_assets),
        status='pending' if row.pending_snapshot else 'approved' if row.published_snapshot else 'rejected' if row.review_note else 'draft',
        published=bool(row.published_snapshot),review_note=row.review_note,
        has_changes=row.published_snapshot!=dict(name=row.name,asset_ids=row.draft_assets),
        assets=[a.jsonl(request=request) for a in StickerAsset.objects.filter(id__in=row.draft_assets)],
        published_snapshot=row.published_snapshot)
    if admin:value.update(pending_snapshot=row.pending_snapshot,owner=row.user.tiny_json(),submitted_at=row.submitted_at.timestamp() if row.submitted_at else None)
    return value


def submit(row,action='publish'):
    if row.pending_snapshot is not None:raise StickerErrors.NOT_ACCESSIBLE
    if action=='publish':
        ids=row.draft_assets
        if not ids or len(ids)>200 or UserSticker.objects.filter(user=row.user,asset_id__in=ids,is_uploaded=True).count()!=len(set(ids)):
            raise StickerErrors.NOT_ACCESSIBLE
    row.pending_snapshot=dict(action=action,name=row.name,asset_ids=list(row.draft_assets))
    row.submitted_at=timezone.now();row.review_note=''
    row.save(update_fields=['pending_snapshot','submitted_at','review_note'])
    from utils.notificator_integration import send_sticker_series_review_mail
    transaction.on_commit(lambda:send_sticker_series_review_mail(row),robust=True)


class StickerSeriesView(View):
    @auth.require_user
    def get(self,request):return [payload(row,request) for row in StickerSeries.objects.filter(user=request.user)]

    @auth.require_user
    def post(self,request):
        name=series_name(data(request).get('name'))
        if StickerSeries.objects.filter(user=request.user).count()>=50:raise StickerErrors.INVALID_HASH
        row,_=StickerSeries.objects.get_or_create(user=request.user,name=name)
        return payload(row,request)


class StickerSeriesDetailView(View):
    @auth.require_user
    def post(self,request,series_id):
        value=data(request)
        try:
            with transaction.atomic():
                row=owned(request,series_id)
                if value.get('action')=='submit':submit(row)
                elif 'remove_asset_id' in value:
                    row.draft_assets=[i for i in row.draft_assets if i!=value['remove_asset_id']]
                    row.save(update_fields=['draft_assets'])
                    release_unused_membership(row)
                else:
                    row.name=series_name(value.get('name'));row.save(update_fields=['name'])
        except IntegrityError:raise StickerErrors.INVALID_HASH
        return payload(row,request)

    @auth.require_user
    def delete(self,request,series_id):
        with transaction.atomic():
            row=owned(request,series_id)
            if row.published_snapshot:submit(row,'delete')
            else:row.delete()
        return OK


class StickerSeriesAssignView(View):
    @auth.require_user
    def post(self,request):
        value=data(request);ids,series_id=value.get('sticker_ids'),value.get('series_id')
        if not isinstance(ids,list) or not 1<=len(ids)<=200 or any(type(i) is not int for i in ids):raise StickerErrors.INVALID_HASH
        with transaction.atomic():
            if type(series_id) is not int:raise StickerErrors.INVALID_HASH
            group=owned(request,series_id)
            rows=list(UserSticker.objects.select_for_update().filter(id__in=set(ids),user=request.user,is_uploaded=True))
            if len(rows)!=len(set(ids)):raise StickerErrors.NOT_ACCESSIBLE
            claim_membership(group,rows)
            assets=list(dict.fromkeys([*group.draft_assets,*[r.asset_id for r in rows]]))
            if len(assets)>200:raise StickerErrors.INVALID_HASH
            group.draft_assets=assets;group.save(update_fields=['draft_assets'])
        return OK


class StickerPacksView(View):
    @auth.require_user
    def get(self,request):
        return [dict(series_id=row.id,name=row.published_snapshot['name'],count=len(row.published_snapshot['asset_ids'])) for row in
            StickerSeries.objects.filter(published_snapshot__isnull=False)]


class StickerPackView(View):
    @auth.require_user
    def get(self,request,series_id):
        row=StickerSeries.objects.filter(id=series_id,published_snapshot__isnull=False).first()
        if not row:raise StickerErrors.NOT_FOUND
        return dict(series_id=row.id,name=row.published_snapshot['name'],assets=[a.jsonl(request=request) for a in StickerAsset.objects.filter(id__in=row.published_snapshot['asset_ids'])])

    @auth.require_user
    def post(self,request,series_id):
        row=StickerSeries.objects.filter(id=series_id,published_snapshot__isnull=False).first()
        if not row:raise StickerErrors.NOT_FOUND
        with transaction.atomic():
            for asset in StickerAsset.objects.filter(id__in=row.published_snapshot['asset_ids']):UserSticker.collect(request.user,asset)
        return OK


class StickerPackReviewView(View):
    @auth.require_platform_admin
    def get(self,request):return [payload(row,request,True) for row in StickerSeries.objects.filter(pending_snapshot__isnull=False).select_related('user').order_by('submitted_at')]

    @auth.require_platform_admin
    def post(self,request,series_id):
        value=data(request)
        if type(value.get('approved')) is not bool:raise StickerErrors.INVALID_HASH
        from PlatformAdmin.models import PlatformAuditLog
        with transaction.atomic():
            row=StickerSeries.objects.select_for_update().filter(id=series_id,pending_snapshot__isnull=False).first()
            if not row:raise StickerErrors.NOT_FOUND
            snapshot=row.pending_snapshot
            if value['approved']:
                if snapshot['action']=='delete':row.published_snapshot=None
                else:row.published_snapshot=dict(name=snapshot['name'],asset_ids=snapshot['asset_ids'])
            row.pending_snapshot=None;row.review_note=str(value.get('note',''))[:300]
            row.save(update_fields=['published_snapshot','pending_snapshot','review_note'])
            release_unused_membership(row)
            PlatformAuditLog.objects.create(action='sticker.series.review',target_type='sticker_series',target_id=row.id,summary=row.name,
                metadata={'actor':request.platform_admin_email,'approved':value['approved'],'action':snapshot['action']})
            if value['approved'] and snapshot['action']=='delete':row.delete()
        return OK


def collect_uploaded(request,asset):
    """A collection upload records provenance without adding it to the user's home."""
    value=data(request);series_id=value.get('series_id')
    with transaction.atomic():
        group=owned(request,series_id) if series_id is not None else None
        if group and len(group.draft_assets)>=200 and asset.id not in group.draft_assets:raise StickerErrors.INVALID_HASH
        sticker,_=UserSticker.collect(request.user,asset,in_home=group is None,uploaded=True)
        sticker=UserSticker.objects.select_for_update().get(id=sticker.id)
        if group:claim_membership(group,[sticker])
        if group and asset.id not in group.draft_assets:
            group.draft_assets=[*group.draft_assets,asset.id];group.save(update_fields=['draft_assets'])
        return sticker
