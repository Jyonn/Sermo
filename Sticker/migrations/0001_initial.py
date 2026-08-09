from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [('User', '0051_replace_legacy_preset_avatars')]
    operations = [
        migrations.CreateModel(
            name='StickerAsset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content_hash', models.CharField(db_index=True, max_length=64, unique=True)),
                ('storage_key', models.CharField(max_length=255)),
                ('mime_type', models.CharField(blank=True, default='image/png', max_length=100)),
                ('file_size', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='UserSticker',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('asset', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='owners', to='Sticker.stickerasset')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stickers', to='User.user')),
            ],
            options={'ordering': ['-created_at', '-id']},
        ),
        migrations.AddConstraint(
            model_name='usersticker',
            constraint=models.UniqueConstraint(fields=('user', 'asset'), name='sticker_unique_user_asset'),
        ),
    ]
