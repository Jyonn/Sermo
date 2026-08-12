from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0016_mediametadata'),
        ('Square', '0005_unify_media_metadata'),
    ]

    operations = [
        migrations.DeleteModel(name='ImageMetadata'),
        migrations.DeleteModel(name='VideoMetadata'),
    ]
