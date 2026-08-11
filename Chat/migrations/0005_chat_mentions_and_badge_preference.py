from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('Chat', '0004_chatuserpreference_notifications_muted'),
        ('Message', '0015_message_sync_events'),
        ('User', '0051_replace_legacy_preset_avatars'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatuserpreference',
            name='unread_badge_muted',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='ChatMessageMention',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('chat', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='message_mentions', to='Chat.chat')),
                ('message', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_mentions', to='Message.message')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_mentions', to='User.user')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('message', 'user'), name='unique_message_mention_user')],
            },
        ),
    ]
