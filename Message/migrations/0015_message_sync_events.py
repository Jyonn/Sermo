from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0014_alter_message_type'),
        ('Chat', '0001_initial'),
        ('User', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MessageUserState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('hidden_at', models.DateTimeField(auto_now_add=True)),
                ('message', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hidden_states', to='Message.message')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hidden_message_states', to='User.user')),
            ],
        ),
        migrations.CreateModel(
            name='MessageEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type', models.IntegerField(choices=[(0, 0), (1, 1), (2, 2)])),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='message_events', to='User.user')),
                ('chat', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='message_events', to='Chat.chat')),
                ('message', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sync_events', to='Message.message')),
                ('target_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='targeted_message_events', to='User.user')),
            ],
            options={'default_manager_name': 'objects'},
        ),
        migrations.AddConstraint(
            model_name='messageuserstate',
            constraint=models.UniqueConstraint(fields=('message', 'user'), name='message_user_hidden_unique'),
        ),
    ]
