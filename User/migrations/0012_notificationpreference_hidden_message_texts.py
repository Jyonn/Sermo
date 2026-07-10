from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0011_notificationpreference_hide_message_content'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='hidden_direct_message_text',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='notificationpreference',
            name='hidden_group_message_text',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
