import diq.diq
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Space', '0003_space_member_limit'),
        ('User', '0012_notificationpreference_hidden_message_texts'),
    ]

    operations = [
        migrations.CreateModel(
            name='PushDevice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(db_index=True, default='getui', max_length=32)),
                ('client_id', models.CharField(db_index=True, max_length=128)),
                ('platform', models.CharField(default='android', max_length=32)),
                ('device_id', models.CharField(blank=True, default='', max_length=128)),
                ('app_version', models.CharField(blank=True, default='', max_length=32)),
                ('enabled', models.BooleanField(db_index=True, default=True)),
                ('last_seen_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='push_devices', to='Space.space')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='push_devices', to='User.user')),
            ],
            options={
                'unique_together': {('provider', 'client_id')},
                'abstract': False,
                'default_manager_name': 'objects',
            },
            bases=(models.Model, diq.diq.Dictify),
        ),
        migrations.CreateModel(
            name='PushDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.IntegerField(choices=[(0, 0), (1, 1), (2, 2), (3, 3)], db_index=True, default=0)),
                ('detail', models.CharField(blank=True, max_length=255, null=True)),
                ('attempted_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='User.pushdevice')),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='push_deliveries', to='User.notificationevent')),
            ],
            options={
                'abstract': False,
                'default_manager_name': 'objects',
            },
            bases=(models.Model, diq.diq.Dictify),
        ),
    ]
