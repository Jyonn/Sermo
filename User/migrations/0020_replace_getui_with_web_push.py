from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0019_notificationpreference_friend_online_message_text'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL('DROP TABLE IF EXISTS User_pushdelivery')],
            state_operations=[migrations.DeleteModel(name='PushDelivery')],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL('DROP TABLE IF EXISTS User_pushdevice')],
            state_operations=[migrations.DeleteModel(name='PushDevice')],
        ),
        migrations.CreateModel(
            name='WebPushSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('endpoint', models.TextField()),
                ('endpoint_digest', models.CharField(max_length=64, unique=True)),
                ('p256dh', models.CharField(max_length=255)),
                ('auth', models.CharField(max_length=255)),
                ('origin', models.CharField(max_length=255)),
                ('user_agent', models.CharField(blank=True, default='', max_length=255)),
                ('enabled', models.BooleanField(db_index=True, default=True)),
                ('last_seen_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='web_push_subscriptions', to='Space.space')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='web_push_subscriptions', to='User.user')),
            ],
            options={'default_manager_name': 'objects'},
        ),
        migrations.CreateModel(
            name='WebPushDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.IntegerField(choices=[(0, 0), (1, 1), (2, 2), (3, 3)], db_index=True, default=0)),
                ('detail', models.CharField(blank=True, max_length=255, null=True)),
                ('attempted_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='web_push_deliveries', to='User.notificationevent')),
                ('subscription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='User.webpushsubscription')),
            ],
            options={'default_manager_name': 'objects'},
        ),
    ]
