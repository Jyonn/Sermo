import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('User', '0035_user_language_preference'),
    ]

    operations = [
        migrations.CreateModel(
            name='MapCheckIn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('region_code', models.CharField(max_length=80)),
                ('region_name', models.CharField(max_length=120)),
                ('country_code', models.CharField(db_index=True, max_length=3)),
                ('country_name', models.CharField(max_length=120)),
                ('checked_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='map_checkins', to='User.user')),
            ],
            options={'ordering': ['country_code', 'region_name', 'id']},
        ),
        migrations.CreateModel(
            name='MapAccessGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('granted_at', models.DateTimeField(auto_now_add=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='map_grants_given', to='User.user')),
                ('viewer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='map_grants_received', to='User.user')),
            ],
        ),
        migrations.AddConstraint(
            model_name='mapcheckin',
            constraint=models.UniqueConstraint(fields=('user', 'region_code'), name='travel_map_unique_user_region'),
        ),
        migrations.AddConstraint(
            model_name='mapaccessgrant',
            constraint=models.UniqueConstraint(fields=('owner', 'viewer'), name='travel_map_unique_owner_viewer'),
        ),
    ]
