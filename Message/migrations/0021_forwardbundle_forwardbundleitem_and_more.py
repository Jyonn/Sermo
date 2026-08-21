import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Chat', '0006_chatuserpreference_statement_reminder'),
        ('Message', '0020_messagehistoryrecovery_and_restored_event'),
        ('User', '0065_remove_legacy_bark_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='ForwardBundle',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='forward_bundles', to='User.user')),
                ('source_chat', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='forward_bundles', to='Chat.chat')),
            ],
            options={'default_manager_name': 'objects'},
        ),
        migrations.AddField(
            model_name='message',
            name='forward_bundle',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='messages', to='Message.forwardbundle'),
        ),
        migrations.CreateModel(
            name='ForwardBundleItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('position', models.PositiveIntegerField(default=0)),
                ('message_type', models.IntegerField()),
                ('author', models.JSONField(default=dict)),
                ('content', models.CharField(blank=True, default='', max_length=512)),
                ('payload', models.JSONField(default=dict)),
                ('sent_at', models.DateTimeField()),
                ('bundle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='Message.forwardbundle')),
                ('media_asset', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='forward_items', to='Message.mediaasset')),
                ('original_message', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='forward_snapshots', to='Message.message')),
            ],
            options={'ordering': ['position']},
        ),
        migrations.AddConstraint(
            model_name='forwardbundleitem',
            constraint=models.UniqueConstraint(fields=('bundle', 'position'), name='forward_bundle_unique_position'),
        ),
        migrations.AlterField(
            model_name='message',
            name='type',
            field=models.IntegerField(choices=[(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10)]),
        ),
    ]
