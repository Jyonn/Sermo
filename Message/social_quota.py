"""Weekly quota belongs to the viewer making an upstream request, never a sender."""
import datetime
import os
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone

WEEKLY_LIMIT = 7
ZONE = ZoneInfo('Asia/Shanghai')

def week_start():
    today = timezone.now().astimezone(ZONE).date()
    return today - datetime.timedelta(days=today.weekday())


def configured():
    return bool(os.environ.get('JUSTONEAPI_TOKEN'))


def used_count(user, week):
    from Message.models import SocialParseUsage
    return SocialParseUsage.objects.filter(user=user, week=week).values_list('count', flat=True).first() or 0


def eligibility(user, week=None):
    if not user or not user.is_permanent_vip:
        return 'vip_required'
    return 'weekly_limit' if used_count(user, week or week_start()) >= WEEKLY_LIMIT else ''


def quota_for(user):
    week = week_start()
    reset = datetime.datetime.combine(week + datetime.timedelta(days=7), datetime.time.min, tzinfo=ZONE)
    used = used_count(user, week) if user.is_permanent_vip else 0
    reason = ('unavailable' if not configured() else 'vip_required' if not user.is_permanent_vip else
              'exhausted' if used >= WEEKLY_LIMIT else None)
    remaining = max(0, WEEKLY_LIMIT - used) if user.is_permanent_vip else 0
    return dict(limit=WEEKLY_LIMIT, used=used, remaining=remaining,
                reset_at=reset.timestamp(), reason=reason)


def consume(user_id):
    from User.models import User
    from Message.models import SocialParseUsage
    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user_id)
        week = week_start()
        error = eligibility(user, week)
        if error:
            return error
        usage, _ = SocialParseUsage.objects.get_or_create(user=user, week=week)
        usage.count += 1
        usage.save(update_fields=['count'])
    return ''
