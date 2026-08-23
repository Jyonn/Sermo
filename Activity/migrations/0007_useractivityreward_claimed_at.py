from django.db import migrations, models
from django.utils import timezone


def mark_existing_rewards_claimed(apps, schema_editor):
    UserActivityReward = apps.get_model('Activity', 'UserActivityReward')
    UserActivityReward.objects.filter(claimed_at__isnull=True).update(claimed_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [('Activity', '0006_expand_baxian_awakening_rounds')]

    operations = [
        migrations.AddField(
            model_name='useractivityreward',
            name='claimed_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(mark_existing_rewards_claimed, migrations.RunPython.noop),
    ]
