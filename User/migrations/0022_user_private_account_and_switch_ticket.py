from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0021_notificationpreference_message_titles'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='is_private_account',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='AccountSwitchTicket',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(db_index=True, max_length=96, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('source_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='issued_account_switch_tickets', to='User.user')),
                ('target_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='account_switch_tickets', to='User.user')),
            ],
            options={
                'abstract': False,
                'default_manager_name': 'objects',
            },
        ),
    ]
