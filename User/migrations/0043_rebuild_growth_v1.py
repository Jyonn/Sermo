import re

from django.db import migrations

from User.growth import (
    DAILY_GROWTH_LIMIT,
    GROWTH_THRESHOLDS,
    WEEKLY_GROWTH_LIMIT,
    resolve_event_rule,
)


def level_for_score(score):
    return max(index + 1 for index, threshold in enumerate(GROWTH_THRESHOLDS) if score >= threshold)


def rebuild_growth(apps, schema_editor):
    User = apps.get_model('User', 'User')
    GrowthEvent = apps.get_model('User', 'GrowthEvent')

    User.objects.update(growth_score=0, growth_level=1)
    for user in User.objects.all().iterator():
        if user.role == 0:
            user.growth_score = GROWTH_THRESHOLDS[-1]
            user.growth_level = 18
            user.growth_acknowledged_level = min(user.growth_acknowledged_level, 18)
            user.save(update_fields=['growth_score', 'growth_level', 'growth_acknowledged_level'])
            continue

        total = 0
        daily_totals = {}
        weekly_totals = {}
        events = GrowthEvent.objects.filter(user_id=user.id).order_by('created_at', 'id')
        for event in events.iterator():
            rule = resolve_event_rule(event.event_key)
            if rule is None:
                event.delete()
                continue

            points = rule.points
            if rule.period == 'daily':
                period_key = event.event_key.split(':')[2]
                points = min(points, max(0, DAILY_GROWTH_LIMIT - daily_totals.get(period_key, 0)))
                daily_totals[period_key] = daily_totals.get(period_key, 0) + points
            elif rule.period == 'weekly':
                period_key = next(
                    (part for part in event.event_key.split(':') if re.fullmatch(r'\d{4}-W\d{2}', part)),
                    '',
                )
                points = min(points, max(0, WEEKLY_GROWTH_LIMIT - weekly_totals.get(period_key, 0)))
                weekly_totals[period_key] = weekly_totals.get(period_key, 0) + points

            event.category = rule.category
            event.title = rule.title
            event.points = points
            event.save(update_fields=['category', 'title', 'points'])
            total += points

        if not user.password:
            cap = 3
        elif user.email_verified_at is None:
            cap = 6
        elif user.phone_verified_at is None:
            cap = 9
        else:
            cap = 18
        level = min(level_for_score(total), cap)
        user.growth_score = total
        user.growth_level = level
        user.growth_acknowledged_level = min(user.growth_acknowledged_level, level)
        user.save(update_fields=['growth_score', 'growth_level', 'growth_acknowledged_level'])


class Migration(migrations.Migration):
    dependencies = [('User', '0042_retire_future_chat_bubbles')]

    operations = [migrations.RunPython(rebuild_growth, migrations.RunPython.noop)]
