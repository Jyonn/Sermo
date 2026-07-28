from django.db import migrations, models
import django.db.models.deletion


def create_campaign(apps, schema_editor):
    apps.get_model('User', 'PermanentVipCampaign').objects.get_or_create(
        key='founding-100',
        defaults={'claimed_count': 0},
    )


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0032_user_growth_acknowledged_level'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='is_permanent_vip',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.CreateModel(
            name='PermanentVipCampaign',
            fields=[
                ('key', models.CharField(default='founding-100', max_length=32, primary_key=True, serialize=False)),
                ('claimed_count', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'default_manager_name': 'objects'},
        ),
        migrations.CreateModel(
            name='PermanentVipClaim',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slot', models.PositiveSmallIntegerField(unique=True)),
                ('claimed_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='permanent_vip_claim', to='User.user')),
            ],
            options={'default_manager_name': 'objects'},
        ),
        migrations.RunPython(create_campaign, migrations.RunPython.noop),
    ]
