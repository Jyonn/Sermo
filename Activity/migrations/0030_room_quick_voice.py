from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Activity', '0029_activity_rerun_request')]

    operations = [
        migrations.AddField(
            model_name=model_name, name='quick_voice_events',
            field=models.JSONField(null=True, blank=True),
        )
        for model_name in ('numberduel', 'landlordtable', 'fourplayertable')
    ]
