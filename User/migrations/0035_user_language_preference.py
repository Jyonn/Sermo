from django.db import migrations, models

import User.validators


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0034_user_personalization'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='language_preference',
            field=models.CharField(
                default='system',
                max_length=16,
                validators=[User.validators.UserValidator.language_preference],
            ),
        ),
    ]
