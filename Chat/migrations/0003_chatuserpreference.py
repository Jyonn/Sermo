from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('Chat', '0002_auto_accept_pending_group_members'),
        ('User', '0018_usergesturelockpreference_decoy'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatUserPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('pinned', models.BooleanField(default=False)),
                ('online_reminder_enabled', models.BooleanField(default=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('chat', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_preferences', to='Chat.chat')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_preferences', to='User.user')),
            ],
            options={
                'unique_together': {('chat', 'user')},
            },
        ),
    ]
