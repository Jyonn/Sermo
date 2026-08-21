import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def backfill_asset_ownership_and_content(apps, schema_editor):
    Message = apps.get_model('Message', 'Message')
    MediaAsset = apps.get_model('Message', 'MediaAsset')
    ForwardBundleItem = apps.get_model('Message', 'ForwardBundleItem')

    for asset in MediaAsset.objects.filter(owner__isnull=True).iterator(chunk_size=500):
        message = Message.objects.filter(media_asset_id=asset.id).order_by('created_at', 'id').first()
        if message is not None:
            asset.owner_id = message.user_id
            asset.save(update_fields=['owner'])

    media_types = (1, 2, 4, 5)
    Message.objects.filter(media_asset__isnull=False, type__in=media_types).update(content='')
    for asset in MediaAsset.objects.iterator(chunk_size=500):
        references = Message.objects.filter(media_asset_id=asset.id, is_deleted=False).count()
        references += ForwardBundleItem.objects.filter(
            media_asset_id=asset.id,
            bundle__messages__is_deleted=False,
        ).count()
        MediaAsset.objects.filter(id=asset.id).update(reference_count=references)


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0021_forwardbundle_forwardbundleitem_and_more'),
        ('User', '0065_remove_legacy_bark_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='mediaasset',
            name='content_hash',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='mediaasset',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=timezone.now, db_index=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='mediaasset',
            name='library_active',
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name='mediaasset',
            name='owner',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='media_assets', to='User.user'),
        ),
        migrations.AddField(
            model_name='mediaasset',
            name='reference_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='message',
            name='content',
            field=models.CharField(blank=True, default='', max_length=512),
        ),
        migrations.RunPython(backfill_asset_ownership_and_content, migrations.RunPython.noop),
    ]
