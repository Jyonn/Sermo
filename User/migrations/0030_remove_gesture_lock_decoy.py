from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0029_user_chat_background'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='usergesturelockpreference',
            name='decoy_enabled',
        ),
        migrations.RemoveField(
            model_name='usergesturelockpreference',
            name='decoy_pattern_hash',
        ),
        migrations.RemoveField(
            model_name='usergesturelockpreference',
            name='decoy_salt',
        ),
    ]
