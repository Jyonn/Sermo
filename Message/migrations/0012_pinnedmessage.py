import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Chat', '0003_chatuserpreference'),
        ('Message', '0011_videometadata'),
        ('User', '0031_user_emoji_usage'),
    ]

    operations = [
        migrations.CreateModel(
            name='PinnedMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('pinned_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('chat', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pinned_messages', to='Chat.chat')),
                ('message', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='pin', to='Message.message')),
                ('pinned_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pinned_messages', to='User.user')),
            ],
            options={'ordering': ['-pinned_at']},
        ),
    ]
