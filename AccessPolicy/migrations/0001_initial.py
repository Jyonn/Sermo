from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [('Space', '0006_space_feature_policies')]

    operations = [
        migrations.CreateModel(
            name='PlatformCapabilityPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('capability_key', models.CharField(db_index=True, max_length=160, unique=True)),
                ('requirement', models.JSONField(blank=True, default=dict)),
                ('denial', models.JSONField(blank=True, default=dict)),
                ('limits', models.JSONField(blank=True, default=dict)),
                ('version', models.PositiveIntegerField(default=1)),
                ('updated_by', models.EmailField(blank=True, default='', max_length=254)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['capability_key']},
        ),
        migrations.CreateModel(
            name='SpaceCapabilityPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('capability_key', models.CharField(db_index=True, max_length=160)),
                ('requirement', models.JSONField(blank=True, default=dict)),
                ('denial', models.JSONField(blank=True, default=dict)),
                ('limits', models.JSONField(blank=True, default=dict)),
                ('version', models.PositiveIntegerField(default=1)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='capability_policies', to='Space.space')),
            ],
            options={'ordering': ['capability_key']},
        ),
        migrations.CreateModel(
            name='CapabilityPolicyAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scope', models.CharField(choices=[('platform', 'Platform'), ('space', 'Space')], db_index=True, max_length=16)),
                ('capability_key', models.CharField(db_index=True, max_length=160)),
                ('actor', models.CharField(blank=True, default='', max_length=255)),
                ('previous', models.JSONField(blank=True, default=dict)),
                ('current', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('space', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='Space.space')),
            ],
            options={'ordering': ['-id']},
        ),
        migrations.AddConstraint(
            model_name='spacecapabilitypolicy',
            constraint=models.UniqueConstraint(fields=('space', 'capability_key'), name='access_policy_unique_space_capability'),
        ),
    ]
