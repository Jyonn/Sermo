from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0056_notificationchannelcursor'),
    ]

    operations = [
        migrations.AlterField(
            model_name='webpushsubscription',
            name='endpoint_digest',
            field=models.CharField(db_index=True, max_length=64),
        ),
        migrations.AddConstraint(
            model_name='webpushsubscription',
            constraint=models.UniqueConstraint(
                fields=('user', 'endpoint_digest'),
                name='unique_user_web_push_endpoint',
            ),
        ),
    ]
