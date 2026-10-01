import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Chat', '0017_chat_is_space_group'),
        ('Message', '0035_linkpreview_provider_data'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatBlockedWord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('word', models.CharField(max_length=80)),
                ('normalized', models.CharField(max_length=80)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('chat', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='blocked_words', to='Chat.chat')),
                ('owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='direct_chat_blocked_words', to='User.user')),
            ],
            options={'indexes': [models.Index(fields=['chat', 'owner'], name='Chat_chat_owner_word_idx')]},
        ),
        migrations.CreateModel(
            name='ChatBlockedWordRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('word', models.CharField(max_length=80)),
                ('normalized', models.CharField(max_length=80)),
                ('status', models.CharField(default='pending', max_length=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('applicant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='blocked_word_requests', to='User.user')),
                ('chat', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='blocked_word_requests', to='Chat.chat')),
                ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='blocked_word_request', to='Message.message')),
            ],
            options={'indexes': [models.Index(fields=['chat', 'status'], name='Chat_chat_status_word_idx')]},
        ),
    ]
