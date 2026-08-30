from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Message', '0027_remove_mediaassetalias')]
    operations = [
        migrations.AddField(model_name='mediaasset', name='original_key', field=models.CharField(blank=True, default='', max_length=255)),
        migrations.AddField(model_name='mediaasset', name='original_uri', field=models.CharField(blank=True, default='', max_length=500)),
        migrations.AddField(model_name='mediaasset', name='playback_key', field=models.CharField(blank=True, default='', max_length=255)),
        migrations.AddField(model_name='mediaasset', name='playback_uri', field=models.CharField(blank=True, default='', max_length=500)),
        migrations.AddField(model_name='mediaasset', name='transcode_status', field=models.IntegerField(db_index=True, default=0)),
        migrations.AddField(model_name='mediaasset', name='transcode_persistent_id', field=models.CharField(blank=True, default='', max_length=128)),
        migrations.AddField(model_name='mediaasset', name='transcode_error', field=models.CharField(blank=True, default='', max_length=500)),
        migrations.AddField(model_name='mediaasset', name='transcoded_at', field=models.DateTimeField(blank=True, db_index=True, null=True)),
        migrations.AddField(model_name='mediaasset', name='original_deleted_at', field=models.DateTimeField(blank=True, null=True)),
    ]
