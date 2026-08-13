from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0054_disable_legacy_subdomain_web_push'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='show_self_avatar',
            field=models.BooleanField(default=False),
        ),
    ]
