from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Message', '0038_messageretention')]
    operations = [
        migrations.AddField(model_name='mediaasset', name='original_file_size', field=models.BigIntegerField(null=True, blank=True)),
        migrations.AddField(model_name='mediaasset', name='transcode_attempts', field=models.PositiveSmallIntegerField(default=0)),
    ]
