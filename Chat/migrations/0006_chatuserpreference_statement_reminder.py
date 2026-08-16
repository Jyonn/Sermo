from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Chat', '0005_chat_mentions_and_badge_preference')]
    operations = [
        migrations.AddField(
            model_name='chatuserpreference',
            name='statement_reminder_enabled',
            field=models.BooleanField(default=False),
        ),
    ]
