from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0014_alter_pushdevice_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='open_chat_on_tap',
            field=models.BooleanField(default=True),
        ),
    ]
