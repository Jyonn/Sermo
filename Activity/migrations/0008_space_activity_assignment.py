from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def migrate_activity_assignments(apps, schema_editor):
    Campaign = apps.get_model('Activity', 'ActivityCampaign')
    SpaceActivity = apps.get_model('Activity', 'SpaceActivity')
    Space = apps.get_model('Space', 'Space')

    baxian = Campaign.objects.filter(key='baxian-immortal-force-2026').first()
    if baxian is not None:
        baxian.assignment_mode = 'manual'
        if not baxian.duration_seconds:
            legacy_duration = baxian.ends_at - baxian.starts_at if baxian.starts_at and baxian.ends_at else timedelta(days=15)
            baxian.duration_seconds = max(1, int(legacy_duration.total_seconds()))
        baxian.save(update_fields=['assignment_mode', 'duration_seconds'])

    Campaign.objects.update_or_create(
        key='spider-man-4-preview',
        defaults=dict(
            title='蜘蛛侠：崭新之日',
            title_en='Spider-Man: Brand New Day',
            summary='全新身份，城市新章。',
            summary_en='A new identity. A new chapter in the city.',
            starts_at=None,
            ends_at=None,
            assignment_mode='manual',
            duration_seconds=30 * 24 * 60 * 60,
            event_key='activity.preview',
            daily_user_limit=1,
            config={'theme': 'spider-man-4', 'preview': True},
            enabled=True,
        ),
    )
    Campaign.objects.update_or_create(
        key='permanent-vip-founding-100',
        defaults=dict(
            title='永久 VIP',
            title_en='Permanent VIP',
            summary='创始永久 VIP 限定活动。',
            summary_en='Founding permanent VIP campaign.',
            starts_at=None,
            ends_at=None,
            assignment_mode='automatic',
            duration_seconds=None,
            event_key='activity.permanent-vip',
            daily_user_limit=1,
            config={'theme': 'permanent-vip', 'managed_by': 'permanent_vip'},
            enabled=True,
        ),
    )

    campaigns = list(Campaign.objects.filter(enabled=True))
    now = timezone.now()
    for campaign in campaigns:
        if campaign.duration_seconds is None and campaign.starts_at and campaign.ends_at:
            campaign.duration_seconds = max(1, int((campaign.ends_at - campaign.starts_at).total_seconds()))
        if campaign.key != 'permanent-vip-founding-100':
            campaign.assignment_mode = 'manual'
        campaign.starts_at = None
        campaign.ends_at = None
        campaign.save(update_fields=['assignment_mode', 'duration_seconds', 'starts_at', 'ends_at'])

    for space in Space.objects.all().iterator():
        for campaign in campaigns:
            run = SpaceActivity.objects.filter(space=space, campaign=campaign).first()
            claimed_at = run.created_at if run is not None else now
            ends_at = claimed_at + timedelta(seconds=campaign.duration_seconds) if campaign.duration_seconds else None
            if run is None:
                SpaceActivity.objects.create(
                    space=space,
                    campaign=campaign,
                    claimed_at=claimed_at,
                    ends_at=ends_at,
                )
            else:
                run.claimed_at = claimed_at
                run.ends_at = ends_at
                run.save(update_fields=['claimed_at', 'ends_at'])


class Migration(migrations.Migration):
    dependencies = [
        ('Activity', '0007_useractivityreward_claimed_at'),
        ('Space', '0007_space_admin_phone_space_admin_phone_verified_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='activitycampaign',
            name='assignment_mode',
            field=models.CharField(
                choices=[('automatic', 'Automatic'), ('manual', 'Manual')],
                db_index=True,
                default='manual',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='activitycampaign',
            name='duration_seconds',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='activitycampaign',
            name='starts_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name='activitycampaign',
            name='ends_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='spaceactivity',
            name='claimed_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='spaceactivity',
            name='ends_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(migrate_activity_assignments, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='activitycampaign',
            name='starts_at',
        ),
        migrations.RemoveField(
            model_name='activitycampaign',
            name='ends_at',
        ),
        migrations.AlterField(
            model_name='spaceactivity',
            name='claimed_at',
            field=models.DateTimeField(db_index=True, default=timezone.now),
        ),
    ]
