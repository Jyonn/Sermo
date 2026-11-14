# Foreground voice call identities, reservations and records.

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Chat', '0027_submission_invite_declined'),
        ('Message', '0040_voice_call_messages'),
        ('User', '0098_voice_call_notifications'),
    ]

    operations = [
        migrations.CreateModel(
            name='VoiceCall',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('client_key', models.UUIDField()),
                ('caller_device', models.UUIDField()),
                ('callee_device', models.UUIDField(null=True)),
                ('week', models.DateField()),
                ('state', models.CharField(db_index=True, default='ringing', max_length=16)),
                ('reason', models.CharField(blank=True, max_length=24)),
                ('max_seconds', models.PositiveIntegerField()),
                ('duration_seconds', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField()),
                ('accepted_at', models.DateTimeField(null=True)),
                ('connected_at', models.DateTimeField(null=True)),
                ('ended_at', models.DateTimeField(null=True)),
                ('expires_at', models.DateTimeField()),
                ('deadline_at', models.DateTimeField(null=True)),
                ('caller_seen_at', models.DateTimeField()),
                ('callee_seen_at', models.DateTimeField(null=True)),
                ('media_closed_at', models.DateTimeField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name='VoiceIdentity',
            fields=[
                ('digest', models.CharField(max_length=64, primary_key=True, serialize=False)),
            ],
        ),
        migrations.CreateModel(
            name='VoiceWeeklyQuota',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('week', models.DateField()),
                ('used_seconds', models.PositiveIntegerField(default=0)),
                ('reserved_seconds', models.PositiveIntegerField(default=0)),
            ],
        ),
        migrations.AddField(
            model_name='voicecall',
            name='callee',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='incoming_voice_calls', to='User.user'),
        ),
        migrations.AddField(
            model_name='voicecall',
            name='caller',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='outgoing_voice_calls', to='User.user'),
        ),
        migrations.AddField(
            model_name='voicecall',
            name='chat',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='Chat.chat'),
        ),
        migrations.AddField(
            model_name='voicecall',
            name='message',
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.SET_NULL, to='Message.message'),
        ),
        migrations.AddField(
            model_name='voiceidentity',
            name='active_call',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='Chat.voicecall'),
        ),
        migrations.AddField(
            model_name='voicecall',
            name='callee_identity',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='Chat.voiceidentity'),
        ),
        migrations.AddField(
            model_name='voicecall',
            name='caller_identity',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='Chat.voiceidentity'),
        ),
        migrations.AddField(
            model_name='voiceweeklyquota',
            name='identity',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='Chat.voiceidentity'),
        ),
        migrations.AddConstraint(
            model_name='voicecall',
            constraint=models.UniqueConstraint(fields=('caller', 'client_key'), name='voice_caller_client_unique'),
        ),
        migrations.AddConstraint(
            model_name='voiceweeklyquota',
            constraint=models.UniqueConstraint(fields=('identity', 'week'), name='voice_identity_week_unique'),
        ),
    ]
