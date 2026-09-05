import django.db.models.deletion
from django.db import migrations, models


def move_transcripts_to_assets(apps, schema_editor):
    AudioTranscript = apps.get_model('Message', 'AudioTranscript')
    Message = apps.get_model('Message', 'Message')

    candidates = {}
    orphan_ids = []
    for transcript in AudioTranscript.objects.all().iterator():
        asset_id = Message.objects.filter(id=transcript.message_id).values_list(
            'media_resource__asset_id', flat=True,
        ).first()
        if asset_id is None:
            orphan_ids.append(transcript.id)
            continue
        current = candidates.get(asset_id)
        rank = (
            {1: 3, 0: 2, 2: 1}.get(transcript.status, 0),
            transcript.completed_at or transcript.updated_at or transcript.started_at,
            transcript.id,
        )
        if current is None or rank > current[0]:
            candidates[asset_id] = (rank, transcript)

    keep_ids = {transcript.id for _rank, transcript in candidates.values()}
    AudioTranscript.objects.exclude(id__in=keep_ids).delete()
    if orphan_ids:
        AudioTranscript.objects.filter(id__in=orphan_ids).delete()
    for asset_id, (_rank, transcript) in candidates.items():
        transcript.asset_id = asset_id
        transcript.save(update_fields=['asset'])


def move_transcripts_to_messages(apps, schema_editor):
    AudioTranscript = apps.get_model('Message', 'AudioTranscript')
    Message = apps.get_model('Message', 'Message')

    orphan_ids = []
    for transcript in AudioTranscript.objects.all().iterator():
        message_id = Message.objects.filter(
            media_resource__asset_id=transcript.asset_id,
            type=5,
        ).order_by('id').values_list('id', flat=True).first()
        if message_id is None:
            orphan_ids.append(transcript.id)
            continue
        transcript.message_id = message_id
        transcript.save(update_fields=['message'])
    if orphan_ids:
        AudioTranscript.objects.filter(id__in=orphan_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0032_alter_message_type_alter_welcomemessagetemplate_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='audiotranscript',
            name='message',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='audio_transcript',
                to='Message.message',
            ),
        ),
        migrations.AddField(
            model_name='audiotranscript',
            name='asset',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='audio_transcript',
                to='Message.mediaasset',
            ),
        ),
        migrations.RunPython(move_transcripts_to_assets, move_transcripts_to_messages),
        migrations.RemoveField(
            model_name='audiotranscript',
            name='message',
        ),
        migrations.AlterField(
            model_name='audiotranscript',
            name='asset',
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='audio_transcript',
                to='Message.mediaasset',
            ),
        ),
    ]
