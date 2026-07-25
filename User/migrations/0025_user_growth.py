from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0024_notificationpreference_bark_icon_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='growth_level',
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='user',
            name='growth_score',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
