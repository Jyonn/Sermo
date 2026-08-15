from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0057_webpushsubscription_per_user_endpoint'),
    ]

    operations = [
        migrations.RemoveField(model_name='notificationpreference', name='friend_online_message_text'),
        migrations.RemoveField(model_name='notificationpreference', name='friend_online_message_title'),
        migrations.RemoveField(model_name='notificationpreference', name='hidden_direct_message_text'),
        migrations.RemoveField(model_name='notificationpreference', name='hidden_direct_message_title'),
        migrations.RemoveField(model_name='notificationpreference', name='hidden_group_message_text'),
        migrations.RemoveField(model_name='notificationpreference', name='hidden_group_message_title'),
    ]
