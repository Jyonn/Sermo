"""Weekly quota belongs to the viewer making an upstream request, never a sender."""
import datetime
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone


def week_start():
    today = timezone.now().astimezone(ZoneInfo('Asia/Shanghai')).date()
    return today - datetime.timedelta(days=today.weekday())


def eligibility(user):
    from Message.models import SocialParseUsage
    if not user or not user.is_permanent_vip:
        return 'vip_required'
    usage = SocialParseUsage.objects.filter(user=user, week=week_start()).first()
    return 'weekly_limit' if usage and usage.count >= 7 else ''


def consume(user_id):
    from User.models import User
    from Message.models import SocialParseUsage
    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user_id)
        error = eligibility(user)
        if error:
            return error
        usage, _ = SocialParseUsage.objects.get_or_create(user=user, week=week_start())
        usage.count += 1
        usage.save(update_fields=['count'])
    return ''
