from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0046_notification_topics_and_square_events'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='last_heartbeat',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
