from django.db import migrations

from User.growth import GROWTH_THRESHOLDS


EVENT_KEY = 'vip:permanent'
EVENT_CATEGORY = 'vip'
EVENT_TITLE = '获得永久 VIP'
EVENT_POINTS = 500


def level_for_score(score):
    return max(index + 1 for index, threshold in enumerate(GROWTH_THRESHOLDS) if score >= threshold)


def grant_permanent_vip_growth(apps, schema_editor):
    User = apps.get_model('User', 'User')
    GrowthEvent = apps.get_model('User', 'GrowthEvent')

    vip_users = User.objects.filter(is_permanent_vip=True).exclude(role=0)
    for user in vip_users.iterator():
        event, _ = GrowthEvent.objects.get_or_create(
            user_id=user.id,
            event_key=EVENT_KEY,
            defaults={
                'category': EVENT_CATEGORY,
                'title': EVENT_TITLE,
                'points': EVENT_POINTS,
            },
        )
        changed_fields = []
        for field, value in (
            ('category', EVENT_CATEGORY),
            ('title', EVENT_TITLE),
            ('points', EVENT_POINTS),
        ):
            if getattr(event, field) != value:
                setattr(event, field, value)
                changed_fields.append(field)
        if changed_fields:
            event.save(update_fields=changed_fields)

        total = sum(
            GrowthEvent.objects.filter(user_id=user.id).values_list('points', flat=True)
        )
        if not user.password:
            cap = 3
        elif user.email_verified_at is None:
            cap = 6
        elif user.phone_verified_at is None:
            cap = 9
        else:
            cap = 18
        user.growth_score = total
        user.growth_level = min(level_for_score(total), cap)
        user.save(update_fields=['growth_score', 'growth_level'])


class Migration(migrations.Migration):
    dependencies = [('User', '0044_reset_growth_acknowledgements')]

    operations = [migrations.RunPython(grant_permanent_vip_growth, migrations.RunPython.noop)]
