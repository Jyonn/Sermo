from django.db import migrations


def seed_starry_night_activity(apps, schema_editor):
    Campaign = apps.get_model('Activity', 'ActivityCampaign')
    Campaign.objects.update_or_create(
        key='starry-night-five-evenings-2026',
        defaults=dict(
            title='五夜星旋',
            title_en='Five Nights Under the Stars',
            summary='连续五晚在星光时段聊天，解锁典藏动态背景「星夜回旋」。',
            summary_en='Chat on five consecutive evenings to unlock the Collector living background Starry Rotation.',
            assignment_mode='manual',
            duration_seconds=15 * 24 * 60 * 60,
            event_key='chat.message.send',
            daily_user_limit=1,
            config={
                'theme': 'starry-night',
                'mode': 'starry_night_streak',
                'streak_days': 5,
                'window_start': '20:00',
                'window_end': '24:00',
                'timezone': 'Asia/Shanghai',
                'rarity': 'legendary',
                'reward': {
                    'resource_type': 'background',
                    'reward_id': 'activity.background.starry-night',
                    'resource_key': 'starry-night',
                },
            },
            enabled=True,
        ),
    )


def remove_starry_night_activity(apps, schema_editor):
    apps.get_model('Activity', 'ActivityCampaign').objects.filter(
        key='starry-night-five-evenings-2026',
    ).delete()


class Migration(migrations.Migration):
    dependencies = [('Activity', '0009_friendly_neighbor_activity')]

    operations = [migrations.RunPython(seed_starry_night_activity, remove_starry_night_activity)]
