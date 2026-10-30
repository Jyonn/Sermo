from django.db import migrations


def backfill_welcome_templates(apps, schema_editor):
    """Finish the one-time transition without overwriting configured lists."""
    User = apps.get_model('User', 'User')
    Template = apps.get_model('Message', 'WelcomeMessageTemplate')
    alias = schema_editor.connection.alias
    users = User.objects.using(alias).filter(welcome_message_templates__isnull=True)
    for user in users.select_related('space').iterator(chunk_size=500):
        content = (user.welcome_message or '').strip()
        if not content:
            if user.is_deleted:
                continue
            chinese = user.language in ('zh-CN', 'zh-TW')
            if user.role == 0:
                content = f'欢迎加入{user.space.name}！' if chinese else f'Welcome to {user.space.name}!'
            else:
                content = '我已同意你的好友申请，快来和我聊天吧～' if chinese else 'I accepted your friend request. Come chat with me!'
        Template.objects.using(alias).get_or_create(user_id=user.pk, position=0,
                                                  defaults={'type': 0, 'content': content})


class Migration(migrations.Migration):
    dependencies = [('Message', '0036_messagecopy'), ('User', '0093_user_signature')]
    operations = [migrations.RunPython(backfill_welcome_templates, migrations.RunPython.noop)]
