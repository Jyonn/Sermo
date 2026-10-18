from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('User', '0084_oauthapplicationrevision_icon_url')]
    operations = [
        migrations.AddField(model_name='oauthapplication', name='space_admin_proof_enabled', field=models.BooleanField(default=False)),
        migrations.CreateModel(name='OAuthAuthorizationRequest', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('token_hash', models.CharField(max_length=64, unique=True)),
            ('parameters', models.JSONField()),
            ('purpose', models.CharField(max_length=24, default='join')),
            ('expires_at', models.DateTimeField()),
            ('used_at', models.DateTimeField(null=True, blank=True)),
            ('cancelled_at', models.DateTimeField(null=True, blank=True)),
            ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='Space.space')),
        ], options={'default_manager_name': 'objects'}),
        migrations.AddField(model_name='oauthgrant', name='target_space', field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='Space.space')),
        migrations.AddField(model_name='oauthgrant', name='authorization_request', field=models.OneToOneField(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, related_name='grant', to='User.oauthauthorizationrequest')),
    ]
