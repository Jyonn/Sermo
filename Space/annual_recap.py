"""Year-end message statistics. Personal results never include message bodies."""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db.models import Count
from django.utils import timezone

from Chat.models import ChatMemberStatusChoice, ChatPurposeChoice
from Message.models import Message, MessageTypeChoice


YEAR = 2026
SHANGHAI = ZoneInfo('Asia/Shanghai')
START = datetime(YEAR, 1, 1, tzinfo=SHANGHAI)
CUTOFF = datetime(YEAR, 12, 21, tzinfo=SHANGHAI)
MESSAGE_TYPES = (
    MessageTypeChoice.TEXT, MessageTypeChoice.IMAGE, MessageTypeChoice.FILE,
    MessageTypeChoice.VIDEO, MessageTypeChoice.AUDIO, MessageTypeChoice.LOCATION,
    MessageTypeChoice.STICKER, MessageTypeChoice.FORWARD_BUNDLE,
)


def summarize(user, *, now=None):
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
        'latest_local_time': latest.isoformat() if latest else None,
    }
