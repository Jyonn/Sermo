from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('Square', '0011_statement_chat_record_redacted'),
        ('Space', '0007_space_admin_phone_space_admin_phone_verified_at_and_more'),
        ('User', '0069_unify_notification_deliveries'),
    ]

    operations = [
        migrations.CreateModel(
            name='SquareMute',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(max_length=240)),
                ('muted_until', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_square_mutes', to='User.user')),
                ('revoked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revoked_square_mutes', to='User.user')),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='square_mutes', to='Space.space')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='square_mutes', to='User.user')),
            ],
            options={'ordering': ['-updated_at', '-id']},
        ),
        migrations.AddConstraint(
            model_name='squaremute',
            constraint=models.UniqueConstraint(fields=('space', 'user'), name='unique_square_mute_per_member'),
        ),
    ]
