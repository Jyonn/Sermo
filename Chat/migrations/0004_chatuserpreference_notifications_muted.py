from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Chat', '0003_chatuserpreference'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatuserpreference',
            name='notifications_muted',
            field=models.BooleanField(default=False),
        ),
    ]
