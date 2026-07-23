from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Message', '0008_message_client_message_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='imagemetadata',
            name='file_size',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='imagemetadata',
            name='geocoding_provider',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='imagemetadata',
            name='pixel_height',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='imagemetadata',
            name='pixel_width',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
