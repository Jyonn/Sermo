import diq.diq
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0015_notificationpreference_open_chat_on_tap'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserWebReminderPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sound_enabled', models.BooleanField(default=True)),
                ('title_enabled', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='web_reminder_preference', to='User.user')),
            ],
            options={
                'abstract': False,
                'default_manager_name': 'objects',
            },
            bases=(models.Model, diq.diq.Dictify),
        ),
    ]
