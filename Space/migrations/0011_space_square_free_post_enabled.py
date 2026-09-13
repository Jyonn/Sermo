from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Space', '0010_space_qq_binding_enabled_spacefeaturegrant'),
    ]

    operations = [
        migrations.AddField(
            model_name='space',
            name='square_free_post_enabled',
            field=models.BooleanField(default=True),
        ),
    ]
