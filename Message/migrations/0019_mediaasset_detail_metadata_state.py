from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Message', '0018_mediaasset')]

    operations = [
        migrations.AddField(
            model_name='mediaasset',
            name='detail_metadata_checked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mediaasset',
            name='detail_metadata_error',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
