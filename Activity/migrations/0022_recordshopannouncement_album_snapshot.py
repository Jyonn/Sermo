from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Activity', '0021_record_shop_multiple_editions'),
    ]

    operations = [
        migrations.AddField(
            model_name='recordshopannouncement',
            name='album_snapshot',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
