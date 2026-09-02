from django.db import migrations


def configure_friendly_neighbor(apps, schema_editor):
    Campaign = apps.get_model('Activity', 'ActivityCampaign')
    Campaign.objects.filter(key='spider-man-4-preview').update(
        title='友好邻居计划',
        title_en='Friendly Neighborhood',
        summary='回复广场发言，积累蛛网值，解锁蜘蛛侠主题奖励。',
        summary_en='Reply in Square, earn Web Points, and unlock themed rewards.',
        event_key='square.statement.comment',
        daily_user_limit=25,
        config={
            'theme': 'spider-man-4',
            'mode': 'friendly_neighbor',
            'daily_points_limit': 25,
            'reply_points': [20, 10, 5],
            'user_rewards': [
                {
                    'key': 'spider-frame',
                    'threshold': 75,
                    'resource_type': 'frame',
                    'reward_id': 'activity.spider.frame',
                    'resource_key': 'spider-web',
                },
                {
                    'key': 'spider-profile',
                    'threshold': 100,
                    'resource_type': 'profile',
                    'reward_id': 'activity.spider.profile',
                    'resource_key': 'spider-city',
                },
            ],
        },
    )


class Migration(migrations.Migration):
    dependencies = [('Activity', '0008_space_activity_assignment')]

    operations = [migrations.RunPython(configure_friendly_neighbor, migrations.RunPython.noop)]
