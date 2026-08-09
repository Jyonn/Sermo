from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Space', '0005_expand_growth_levels')]

    operations = [
        migrations.AddField(
            model_name='space',
            name='chat_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='space',
            name='square_explore_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='space',
            name='unverified_group_policy',
            field=models.PositiveSmallIntegerField(default=2),
        ),
    ]
