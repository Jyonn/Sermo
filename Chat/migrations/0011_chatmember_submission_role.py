from django.db import migrations, models


def backfill_submission_member_roles(apps, schema_editor):
    ChatMember = apps.get_model('Chat', 'ChatMember')
    Space = apps.get_model('Space', 'Space')
    SpaceOperator = apps.get_model('Space', 'SpaceOperator')
    Submission = apps.get_model('Chat', 'Submission')

    official_by_space = dict(Space.objects.values_list('id', 'official_user_id'))
    operator_ids = set(SpaceOperator.objects.values_list('user_id', flat=True))

    for submission in Submission.objects.select_related('chat').iterator():
        members = ChatMember.objects.filter(chat_id=submission.chat_id)
        members.filter(user_id=submission.author_id).update(submission_role=0)
        members.filter(user_id=submission.recipient_id).update(submission_role=1)
        for member in members.exclude(user_id__in=(submission.author_id, submission.recipient_id)):
            is_reviewer = (
                member.user_id == official_by_space.get(submission.chat.space_id)
                or member.user_id in operator_ids
            )
            member.submission_role = 1 if is_reviewer else 0
            member.save(update_fields=['submission_role'])


class Migration(migrations.Migration):

    dependencies = [
        ('Chat', '0010_alter_submission_status'),
        ('Space', '0009_space_submission_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatmember',
            name='submission_role',
            field=models.IntegerField(blank=True, choices=[(0, 0), (1, 1)], db_index=True, null=True),
        ),
        migrations.RunPython(backfill_submission_member_roles, migrations.RunPython.noop),
    ]
