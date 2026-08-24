from django.db import migrations, models


def backfill_message_appearance(apps, schema_editor):
    Message = apps.get_model('Message', 'Message')
    User = apps.get_model('User', 'User')
    users = {
        user.id: {
            'chat_bubble_style': user.chat_bubble_style,
            'avatar_frame_style': user.avatar_frame_style,
            'is_permanent_vip': user.is_permanent_vip,
        }
        for user in User.objects.all().only(
            'id', 'chat_bubble_style', 'avatar_frame_style', 'is_permanent_vip',
        )
    }
    batch = []
    for message in Message.objects.filter(appearance={}).only('id', 'user_id', 'appearance').iterator(chunk_size=1000):
        message.appearance = users.get(message.user_id, {})
        batch.append(message)
        if len(batch) >= 1000:
            Message.objects.bulk_update(batch, ['appearance'], batch_size=1000)
            batch.clear()
    if batch:
        Message.objects.bulk_update(batch, ['appearance'], batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [('Message', '0024_alter_message_type_activity')]
    operations = [
        migrations.AddField(
            model_name='message',
            name='appearance',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(backfill_message_appearance, migrations.RunPython.noop),
    ]
