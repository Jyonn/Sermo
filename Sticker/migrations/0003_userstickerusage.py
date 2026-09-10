from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('Sticker', '0002_stickerasset_dimensions'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserStickerUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('use_count', models.PositiveIntegerField(default=0)),
                ('last_used_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('asset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_usages', to='Sticker.stickerasset')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sticker_usages', to='User.user')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('user', 'asset'), name='sticker_unique_user_asset_usage')],
            },
        ),
    ]
