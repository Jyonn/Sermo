import django.db.models.deletion
from django.db import migrations, models


def restore_known_creators(apps, schema_editor):
    Word = apps.get_model('Chat', 'ChatBlockedWord')
    Request = apps.get_model('Chat', 'ChatBlockedWordRequest')
    for word in Word.objects.filter(created_by__isnull=True).iterator():
        creator_id = word.owner_id
        if creator_id is None:
            creator_id = Request.objects.filter(
                chat_id=word.chat_id, normalized=word.normalized, status='approved',
            ).order_by('-id').values_list('applicant_id', flat=True).first()
        if creator_id is not None:
            Word.objects.filter(id=word.id).update(created_by_id=creator_id)


class Migration(migrations.Migration):
    dependencies = [('Chat', '0018_chat_blocked_words')]

    operations = [
        migrations.AddField(
            model_name='chatblockedword',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_chat_blocked_words', to='User.user'),
        ),
        migrations.RunPython(restore_known_creators, migrations.RunPython.noop),
    ]
