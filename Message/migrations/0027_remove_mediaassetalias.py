from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0026_fix_image_capture_timezone'),
    ]

    operations = [
        migrations.DeleteModel(name='MediaAssetAlias'),
    ]
