from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('AccessPolicy', '0004_replace_spaceport_background_policy')]

    operations = [
        migrations.AddField(
            model_name='platformcapabilitypolicy', name='enabled',
            field=models.BooleanField(default=True, db_default=True),
        ),
        migrations.AddField(
            model_name='spacecapabilitypolicy', name='enabled',
            field=models.BooleanField(default=True, db_default=True),
        ),
    ]
