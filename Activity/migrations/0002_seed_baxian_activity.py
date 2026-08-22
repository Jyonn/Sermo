from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def seed_baxian_activity(apps, schema_editor):
    Campaign = apps.get_model('Activity', 'ActivityCampaign')
    Milestone = apps.get_model('Activity', 'ActivityMilestone')
    starts_at = timezone.now()
    campaign, _ = Campaign.objects.update_or_create(
        key='baxian-immortal-force-2026',
        defaults=dict(
            title='八仙聚力',
            title_en='Gather the Immortal Force',
            summary='每天发言凝聚一缕仙力，和整个空间一起唤醒八仙气泡。',
            summary_en='Post each day, gather Immortal Force, and awaken all three Baxian bubbles together.',
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=15),
            event_key='square.statement.publish',
            daily_user_limit=1,
            config={'theme': 'baxian', 'banner': 'baxian-immortal-force'},
            enabled=True,
        ),
    )
    pool = [
        {'reward_id': 'bubble.baxian-lv', 'resource_key': 'baxian-lv'},
        {'reward_id': 'bubble.baxian-zhongli', 'resource_key': 'baxian-zhongli'},
        {'reward_id': 'bubble.baxian-he', 'resource_key': 'baxian-he'},
    ]
    for threshold in (8, 16, 24):
        Milestone.objects.update_or_create(
            campaign=campaign,
            threshold=threshold,
            defaults={
                'resource_type': 'bubble',
                'reward_pool': pool,
                'reward_label': f'随机八仙气泡 · 第 {threshold // 8} 款',
            },
        )


class Migration(migrations.Migration):
    dependencies = [('Activity', '0001_initial')]
    operations = [migrations.RunPython(seed_baxian_activity, migrations.RunPython.noop)]
