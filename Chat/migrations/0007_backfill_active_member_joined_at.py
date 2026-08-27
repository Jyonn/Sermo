from django.db import migrations, models


def backfill_active_member_joined_at(apps, schema_editor):
    ChatMember = apps.get_model('Chat', 'ChatMember')
    ChatMember.objects.filter(status=1, joined_at__isnull=True).update(joined_at=models.F('created_at'))


class Migration(migrations.Migration):
    dependencies = [
        ('Chat', '0006_chatuserpreference_statement_reminder'),
    ]

    operations = [
        migrations.RunPython(backfill_active_member_joined_at, migrations.RunPython.noop),
    ]
