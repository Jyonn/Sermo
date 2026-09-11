from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Chat', '0014_submission_current_round')]

    operations = [
        migrations.AddField(
            model_name='chat',
            name='group_background_theme',
            field=models.CharField(default='default', max_length=16),
        ),
        migrations.AddField(
            model_name='chatuserpreference',
            name='use_personal_background',
            field=models.BooleanField(default=False),
        ),
    ]
