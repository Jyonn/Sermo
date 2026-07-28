from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0031_user_emoji_usage'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='growth_acknowledged_level',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
