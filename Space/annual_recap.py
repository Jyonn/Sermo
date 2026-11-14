"""Year-end message statistics. Personal results never include message bodies."""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db.models import Count
from django.utils import timezone

from Chat.models import Chat, ChatMemberStatusChoice, ChatPurposeChoice
from Message.models import Message, MessageTypeChoice
from Sticker.models import ManagedResource, StickerAsset, StickerSeries


YEAR = 2026
SHANGHAI = ZoneInfo('Asia/Shanghai')
START = datetime(YEAR, 1, 1, tzinfo=SHANGHAI)
CUTOFF = datetime(YEAR, 12, 21, tzinfo=SHANGHAI)
MESSAGE_TYPES = (
    MessageTypeChoice.TEXT, MessageTypeChoice.IMAGE, MessageTypeChoice.FILE,
    MessageTypeChoice.VIDEO, MessageTypeChoice.AUDIO, MessageTypeChoice.LOCATION,
    MessageTypeChoice.STICKER, MessageTypeChoice.FORWARD_BUNDLE,
)


def favorite_public_sticker(messages, request):
    public_ids = {
        asset_id
        for snapshot in StickerSeries.objects.exclude(published_snapshot=None).values_list('published_snapshot', flat=True).iterator()
        for asset_id in snapshot.get('asset_ids', ())
    }
    public_ids.update(ManagedResource.objects.filter(
        collection__published=True, collection__category='emoji', enabled=True, sticker_asset__isnull=False,
    ).values_list('sticker_asset_id', flat=True))
    if not public_ids:
        return None
    counts = {}
    rows = messages.filter(type=MessageTypeChoice.STICKER).order_by().values('content').annotate(count=Count('id'))
    for row in rows.iterator():
        asset_id = Message._parse_payload(row['content']).get('asset_id')
        if isinstance(asset_id, int) and asset_id in public_ids:
            counts[asset_id] = counts.get(asset_id, 0) + row['count']
    if not counts:
        return None
    asset_id = min(counts, key=lambda key: (-counts[key], key))
    asset = StickerAsset.objects.filter(pk=asset_id).first()
    return {'uri': asset.resource_uri(request), 'count': counts[asset_id]} if asset else None


def summarize(user, *, now=None, request=None):
    end = min(now or timezone.now(), CUTOFF)
    space_messages = Message.objects.filter(
        chat__space_id=user.space_id, chat__is_deleted=False, chat__purpose=ChatPurposeChoice.NORMAL,
        is_deleted=False, type__in=MESSAGE_TYPES, created_at__gte=START, created_at__lt=end,
    )
    own_messages = space_messages.filter(
        user=user, chat__chat_members__user=user, chat__chat_members__status=ChatMemberStatusChoice.ACTIVE,
    )
    favorite_chat = own_messages.order_by().values('chat_id').annotate(
        count=Count('id', distinct=True),
    ).order_by('-count', 'chat_id').first()
    if favorite_chat:
        chat = Chat.objects.get(pk=favorite_chat['chat_id'])
        favorite_chat['name'] = chat.title
        if chat.direct:
            favorite_chat['name'] = chat.chat_members.filter(
                status=ChatMemberStatusChoice.ACTIVE, user__is_deleted=False,
            ).exclude(user=user).order_by('id').values_list('user__name', flat=True).first()
    latest = None
    latest_rank = None
    active_dates = set()
    for timestamp in own_messages.order_by().values_list('created_at', flat=True).iterator():
        local = timestamp.astimezone(SHANGHAI)
        active_dates.add(local.date())
        # Treat the hours before dawn as part of the previous night.
        rank = ((local.hour - 6) % 24, local.minute, local.second, local)
        if latest_rank is None or rank > latest_rank:
            latest, latest_rank = local, rank
    return {
        'year': YEAR,
        'through': end.isoformat(),
        'space_message_count': space_messages.count(),
        'personal_message_count': own_messages.distinct().count(),
        'active_days': len(active_dates),
        'favorite_chat': favorite_chat,
        'favorite_public_sticker': favorite_public_sticker(space_messages, request),
        'latest_local_time': latest.isoformat() if latest else None,
    }
