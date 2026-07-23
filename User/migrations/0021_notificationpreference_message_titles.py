from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0020_replace_getui_with_web_push'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='friend_online_message_title',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='notificationpreference',
            name='hidden_direct_message_title',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='notificationpreference',
            name='hidden_group_message_title',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
    ]
