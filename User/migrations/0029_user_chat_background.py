from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0028_userpasswordrecoverychallenge'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='chat_background_theme',
            field=models.CharField(default='default', max_length=16),
        ),
        migrations.AddField(
            model_name='user',
            name='chat_background_uri',
            field=models.CharField(default='', max_length=255),
        ),
    ]
