from django.db import migrations, models
import django.db.models.deletion


HISTORICAL_CAPABILITY_REWARDS = {
    2: ['capability.image'],
    3: ['capability.audio', 'capability.location'],
    4: ['capability.group', 'capability.avatar'],
    5: ['capability.video', 'capability.group_name', 'capability.nickname_365'],
    6: ['capability.welcome', 'capability.sticker'],
    7: ['capability.online'],
    8: ['capability.audio_download', 'capability.nickname_30', 'capability.custom_background'],
    10: ['capability.notification'],
    12: ['capability.nickname_7'],
}


def baseline_existing_users(apps, schema_editor):
    User = apps.get_model('User', 'User')
    Discovery = apps.get_model('User', 'UserFeatureDiscovery')
    rows = []
    for user in User.objects.filter(is_deleted=False).iterator():
        acknowledged = min(user.growth_acknowledged_level, user.growth_level)
        for level, reward_ids in HISTORICAL_CAPABILITY_REWARDS.items():
            if level > acknowledged:
                continue
            rows.extend(Discovery(user_id=user.id, reward_id=reward_id) for reward_id in reward_ids)
        if user.is_permanent_vip and acknowledged:
            rows.append(Discovery(user_id=user.id, reward_id='capability.notification'))
    Discovery.objects.bulk_create(rows, ignore_conflicts=True, batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [('User', '0051_replace_legacy_preset_avatars')]

    operations = [
        migrations.CreateModel(
            name='UserFeatureDiscovery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reward_id', models.CharField(max_length=80)),
                ('discovered_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feature_discoveries', to='User.user')),
            ],
        ),
        migrations.AddConstraint(
            model_name='userfeaturediscovery',
            constraint=models.UniqueConstraint(fields=('user', 'reward_id'), name='user_feature_discovery_unique'),
        ),
        migrations.RunPython(baseline_existing_users, migrations.RunPython.noop),
    ]
