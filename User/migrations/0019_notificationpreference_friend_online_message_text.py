from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0018_usergesturelockpreference_decoy'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='friend_online_message_text',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
