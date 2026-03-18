from django.db import migrations
from django.utils import timezone


def auto_accept_pending_group_members(apps, schema_editor):
    Chat = apps.get_model('Chat', 'Chat')
    ChatMember = apps.get_model('Chat', 'ChatMember')

    group_chat_ids = Chat.objects.filter(chat_type=1).values_list('id', flat=True)
    pending = ChatMember.objects.filter(
        chat_id__in=group_chat_ids,
        status=0,
    )
    pending.update(status=1)
    pending.filter(joined_at__isnull=True).update(joined_at=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ('Chat', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(auto_accept_pending_group_members, migrations.RunPython.noop),
    ]
