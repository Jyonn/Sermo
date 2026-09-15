from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Space', '0011_space_square_free_post_enabled')]
    operations = [
        migrations.AddField(
            model_name='space',
            name='space_group_enabled',
            field=models.BooleanField(default=False),
        ),
    ]
