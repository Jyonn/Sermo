from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Activity', '0010_starry_night_activity')]

    operations = [
        migrations.AddField(
            model_name='spaceactivity',
            name='history_backfilled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
