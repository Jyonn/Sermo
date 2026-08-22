import os

import django.db.models.deletion
from django.db import migrations, models


MEDIA_KIND_BY_MESSAGE_TYPE = {1: 0, 2: 3, 4: 1, 5: 2}


def _resource_for(MediaResource, owner_id, asset_id, kind, file_name):
    normalized_name = os.path.basename(str(file_name or '').strip())[:180]
    resource, _created = MediaResource.objects.get_or_create(
        owner_id=owner_id,
        asset_id=asset_id,
        kind=kind,
        file_name=normalized_name,
        defaults={'library_active': True},
    )
    return resource


def create_resources_and_merge_assets(apps, schema_editor):
    Message = apps.get_model('Message', 'Message')
    MediaAsset = apps.get_model('Message', 'MediaAsset')
    MediaAssetAlias = apps.get_model('Message', 'MediaAssetAlias')
    MediaResource = apps.get_model('Message', 'MediaResource')
    ForwardBundleItem = apps.get_model('Message', 'ForwardBundleItem')
    StatementMedia = apps.get_model('Square', 'StatementMedia')

    for message in Message.objects.exclude(media_asset_id=None).iterator(chunk_size=500):
        kind = MEDIA_KIND_BY_MESSAGE_TYPE.get(message.type)
        if kind is None:
            continue
        asset = MediaAsset.objects.get(id=message.media_asset_id)
        resource = _resource_for(
            MediaResource, message.user_id, asset.id, kind, asset.file_name,
        )
        message.media_resource_id = resource.id
        message.save(update_fields=['media_resource'])

    for item in ForwardBundleItem.objects.exclude(media_asset_id=None).select_related('bundle').iterator(chunk_size=500):
        kind = MEDIA_KIND_BY_MESSAGE_TYPE.get(item.message_type)
        if kind is None:
            continue
        asset = MediaAsset.objects.get(id=item.media_asset_id)
        payload = item.payload if isinstance(item.payload, dict) else {}
        resource = _resource_for(
            MediaResource,
            item.bundle.created_by_id,
            asset.id,
            kind,
            payload.get('file_name') or asset.file_name,
        )
        item.media_resource_id = resource.id
        item.save(update_fields=['media_resource'])

    hashes = MediaAsset.objects.exclude(content_hash='').exclude(content_hash=None).values_list('content_hash', flat=True).distinct()
    for content_hash in hashes.iterator(chunk_size=200):
        assets = list(MediaAsset.objects.filter(content_hash=content_hash).order_by('id'))
        sizes = {asset.file_size for asset in assets}
        if len(sizes) > 1:
            # A real SHA-256 collision is not safe to merge. Keep the oldest
            # hash owner and quarantine the other rows outside the unique key.
            for asset in assets[1:]:
                asset.content_hash = None
                asset.error = ('SHA-256 collision with mismatched file size; ' + (asset.error or ''))[:500]
                asset.save(update_fields=['content_hash', 'error'])
            continue
        if len(assets) < 2:
            continue
        canonical = sorted(
            assets,
            key=lambda asset: (
                asset.status == 1,
                bool(asset.detail_metadata_checked_at),
                -asset.id,
            ),
            reverse=True,
        )[0]
        for duplicate in assets:
            if duplicate.id == canonical.id:
                continue
            MediaAssetAlias.objects.get_or_create(
                slug=duplicate.blob_slug,
                defaults={'asset_id': canonical.id},
            )
            for resource in MediaResource.objects.filter(asset_id=duplicate.id).iterator(chunk_size=200):
                target = MediaResource.objects.filter(
                    owner_id=resource.owner_id,
                    asset_id=canonical.id,
                    kind=resource.kind,
                    file_name=resource.file_name,
                ).first()
                if target is not None:
                    Message.objects.filter(media_resource_id=resource.id).update(media_resource_id=target.id)
                    ForwardBundleItem.objects.filter(media_resource_id=resource.id).update(media_resource_id=target.id)
                    resource.delete()
                else:
                    resource.asset_id = canonical.id
                    resource.save(update_fields=['asset'])
            Message.objects.filter(media_asset_id=duplicate.id).update(media_asset_id=canonical.id)
            ForwardBundleItem.objects.filter(media_asset_id=duplicate.id).update(media_asset_id=canonical.id)
            StatementMedia.objects.filter(media_asset_id=duplicate.id).update(media_asset_id=canonical.id)
            MediaAssetAlias.objects.filter(asset_id=duplicate.id).update(asset_id=canonical.id)
            duplicate.delete()

    for resource in MediaResource.objects.iterator(chunk_size=500):
        references = Message.objects.filter(media_resource_id=resource.id, is_deleted=False).count()
        references += ForwardBundleItem.objects.filter(
            media_resource_id=resource.id,
            bundle__messages__is_deleted=False,
        ).count()
        MediaResource.objects.filter(id=resource.id).update(reference_count=references)


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0022_mediaasset_cloud_library'),
        ('Square', '0006_simplify_statementmedia'),
    ]

    operations = [
        migrations.CreateModel(
            name='MediaResource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.IntegerField(db_index=True)),
                ('file_name', models.CharField(blank=True, default='', max_length=180)),
                ('library_active', models.BooleanField(db_index=True, default=True)),
                ('reference_count', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('asset', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='resources', to='Message.mediaasset')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='media_resources', to='User.user')),
            ],
        ),
        migrations.AddConstraint(
            model_name='mediaresource',
            constraint=models.UniqueConstraint(fields=('owner', 'asset', 'kind', 'file_name'), name='media_resource_owner_asset_kind_name_unique'),
        ),
        migrations.AddField(
            model_name='message',
            name='media_resource',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='messages', to='Message.mediaresource'),
        ),
        migrations.AddField(
            model_name='forwardbundleitem',
            name='media_resource',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='forward_items', to='Message.mediaresource'),
        ),
        migrations.RunPython(create_resources_and_merge_assets, migrations.RunPython.noop),
        migrations.RemoveField(model_name='message', name='media_asset'),
        migrations.RemoveField(model_name='forwardbundleitem', name='media_asset'),
        migrations.RemoveField(model_name='mediaasset', name='owner'),
        migrations.RemoveField(model_name='mediaasset', name='file_name'),
        migrations.RemoveField(model_name='mediaasset', name='library_active'),
        migrations.RemoveField(model_name='mediaasset', name='reference_count'),
        migrations.AlterField(
            model_name='mediaasset',
            name='content_hash',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
    ]
