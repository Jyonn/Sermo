import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0019_mediaasset_detail_metadata_state'),
    ]

    operations = [
        migrations.AlterField(
            model_name='messageevent',
            name='type',
            field=models.IntegerField(choices=[(0, 0), (1, 1), (2, 2), (3, 3)]),
        ),
        migrations.CreateModel(
            name='MessageHistoryRecovery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('restored_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('chat', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='message_history_recoveries', to='Chat.chat')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='message_history_recoveries', to='User.user')),
            ],
            options={'default_manager_name': 'objects'},
        ),
    ]
