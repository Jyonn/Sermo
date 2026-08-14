from django.db import migrations, models
import django.db.models.deletion


def initialize_channel_cursors(apps, schema_editor):
    Message = apps.get_model('Message', 'Message')
    NotificationChannelCursor = apps.get_model('User', 'NotificationChannelCursor')
    NotificationPreference = apps.get_model('User', 'NotificationPreference')
    latest_message_id = Message.objects.order_by('-id').values_list('id', flat=True).first() or 0
    rows = [
        NotificationChannelCursor(
            user_id=user_id,
            channel=channel,
            last_message_id=latest_message_id,
        )
        for user_id, channel in NotificationPreference.objects.values_list('user_id', 'channel')
    ]
    NotificationChannelCursor.objects.bulk_create(rows, ignore_conflicts=True, batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0019_mediaasset_detail_metadata_state'),
        ('User', '0055_user_show_self_avatar'),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationChannelCursor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel', models.IntegerField(choices=[(0, 0), (1, 1), (2, 2), (3, 3)])),
                ('last_message_id', models.BigIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_channel_cursors', to='User.user')),
            ],
            options={'unique_together': {('user', 'channel')}},
        ),
        migrations.RunPython(initialize_channel_cursors, migrations.RunPython.noop),
    ]
