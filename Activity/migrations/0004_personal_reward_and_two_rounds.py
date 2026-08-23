from django.db import migrations, models
import django.db.models.deletion


POOL = [
    {'resource_type': 'bubble', 'reward_id': 'bubble.baxian-lv', 'resource_key': 'baxian-lv'},
    {'resource_type': 'bubble', 'reward_id': 'bubble.baxian-zhongli', 'resource_key': 'baxian-zhongli'},
    {'resource_type': 'bubble', 'reward_id': 'bubble.baxian-he', 'resource_key': 'baxian-he'},
]


def update_baxian_rules(apps, schema_editor):
    Campaign = apps.get_model('Activity', 'ActivityCampaign')
    Milestone = apps.get_model('Activity', 'ActivityMilestone')
    campaign = Campaign.objects.filter(key='baxian-immortal-force-2026').first()
    if not campaign:
        return
    config = dict(campaign.config or {})
    config.update(personal_event_target=2, personal_reward_pool=POOL, awakening_count=8)
    campaign.config = config
    campaign.summary = '发言两次赢取个人气泡，每天凝聚仙力，和整个空间共同唤醒两款八仙气泡。'
    campaign.summary_en = 'Post twice for a personal bubble, then gather daily Force to awaken two more with your space.'
    campaign.save(update_fields=['config', 'summary', 'summary_en'])
    Milestone.objects.filter(campaign=campaign).exclude(threshold__in=(8, 16)).delete()
    for threshold in (8, 16):
        Milestone.objects.update_or_create(
            campaign=campaign,
            threshold=threshold,
            defaults={
                'resource_type': 'bubble',
                'reward_pool': POOL,
                'reward_label': f'随机八仙气泡 · 第 {threshold // 8} 款',
            },
        )


class Migration(migrations.Migration):
    dependencies = [('Activity', '0003_activityawakening')]
    operations = [
        migrations.CreateModel(
            name='UserActivityReward',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('resource_type', models.CharField(max_length=24)),
                ('reward_id', models.CharField(max_length=80)),
                ('resource_key', models.CharField(max_length=80)),
                ('unlocked_at', models.DateTimeField(auto_now_add=True)),
                ('progress', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='personal_reward', to='Activity.useractivityprogress')),
            ],
        ),
        migrations.RunPython(update_baxian_rules, migrations.RunPython.noop),
    ]
