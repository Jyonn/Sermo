from django.db import migrations, models
from django.utils import timezone


def mark_existing_force_claimed(apps, schema_editor):
    ActivityEvent = apps.get_model('Activity', 'ActivityEvent')
    ActivityEvent.objects.filter(points__gt=0, claimed_at__isnull=True).update(claimed_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ('Activity', '0004_personal_reward_and_two_rounds'),
    ]

    operations = [
        migrations.AddField(
            model_name='activityevent',
            name='claimed_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(mark_existing_force_claimed, migrations.RunPython.noop),
    ]
