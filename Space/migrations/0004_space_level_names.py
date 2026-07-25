from django.db import migrations, models

import Space.models


class Migration(migrations.Migration):
    dependencies = [
        ('Space', '0003_space_member_limit'),
    ]

    operations = [
        migrations.AddField(
            model_name='space',
            name='level_names',
            field=models.JSONField(default=Space.models.default_level_names),
        ),
    ]
