import datetime
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from User.validators import UserErrors


def can_view_growth_report(user, level=None):
    return (user.effective_growth_level() if level is None else level) >= 16 or user.resource_inventory.filter(reward_id='growth.report').exists()


def growth_report(user):
    if not can_view_growth_report(user):
        raise UserErrors.GROWTH_REPORT_LOCKED
    growth = user.calculate_growth()
    today = timezone.localdate()
    start = today - datetime.timedelta(days=29)
    events = user.growth_events.filter(points__gt=0)
    categories = list(events.values('category').annotate(points=Sum('points'), count=Count('id')).order_by('-points', 'category'))
    days = {row['day']: row['count'] for row in events.annotate(day=TruncDate('updated_at')).filter(day__gte=start).values('day').annotate(count=Count('id'))}
    return dict(
        level=growth['level'], name=growth['name'], score=growth['score'],
        joined_at=user.created_at.timestamp(),
        days_together=max(1, (today - timezone.localdate(user.created_at)).days + 1),
        record_count=events.count(), categories=categories,
        activity=[{'date': (start + datetime.timedelta(days=i)).isoformat(), 'count': days.get(start + datetime.timedelta(days=i), 0)} for i in range(30)],
        milestones=[item for item in growth['milestones'] if item['earned']],
        recent_events=[item.jsonl() for item in events.order_by('-updated_at', '-id')[:20]],
    )
