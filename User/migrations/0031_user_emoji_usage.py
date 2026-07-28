from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0030_remove_gesture_lock_decoy'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserEmojiUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('emoji', models.CharField(max_length=64)),
                ('use_count', models.PositiveIntegerField(default=0)),
                ('last_used_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='emoji_usages', to='User.user')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'emoji'), name='unique_user_emoji_usage'),
                ],
            },
        ),
    ]
