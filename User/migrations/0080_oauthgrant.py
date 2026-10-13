from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('User', '0079_user_left_space_group_manually')]

    operations = [
        migrations.CreateModel(
            name='OAuthGrant',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('client_id', models.CharField(max_length=80)),
                ('redirect_uri', models.URLField(max_length=512)),
                ('scopes', models.JSONField(default=list)),
                ('code_challenge', models.CharField(max_length=128)),
                ('code_hash', models.CharField(max_length=64, unique=True)),
                ('code_expires_at', models.DateTimeField(db_index=True)),
                ('code_used_at', models.DateTimeField(blank=True, null=True)),
                ('access_token_hash', models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ('access_expires_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='oauth_grants', to='User.user')),
            ],
        ),
    ]
