from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0063_refine_avatar_frame_catalog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='instantnotificationendpoint',
            name='provider',
            field=models.CharField(
                choices=[
                    ('bark', 'bark'),
                    ('ntfy', 'ntfy'),
                    ('gotify', 'gotify'),
                    ('pushdeer', 'pushdeer'),
                ],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name='instantnotificationverification',
            name='provider',
            field=models.CharField(
                choices=[
                    ('bark', 'bark'),
                    ('ntfy', 'ntfy'),
                    ('gotify', 'gotify'),
                    ('pushdeer', 'pushdeer'),
                ],
                max_length=16,
            ),
        ),
    ]
