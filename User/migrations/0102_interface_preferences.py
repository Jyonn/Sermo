from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0101_square_comment_mode')]

    operations = [
        migrations.AddField(
            model_name='user',
            name='interface_preferences',
            field=models.JSONField(null=True, blank=True),
        ),
    ]
