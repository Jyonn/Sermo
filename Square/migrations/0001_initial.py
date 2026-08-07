import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('Space', '0005_expand_growth_levels'),
        ('User', '0045_permanent_vip_growth_reward'),
    ]

    operations = [
        migrations.CreateModel(
            name='Statement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.CharField(blank=True, default='', max_length=140)),
                ('visibility', models.IntegerField(choices=[(0, 0), (1, 1)], db_index=True, default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='statements', to='Space.space')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='statements', to='User.user')),
            ],
            options={'ordering': ['-id']},
        ),
        migrations.CreateModel(
            name='StatementMedia',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.IntegerField(choices=[(0, 0), (1, 1)])),
                ('position', models.PositiveSmallIntegerField(default=0)),
                ('key', models.CharField(max_length=255)),
                ('blob_slug', models.CharField(db_index=True, max_length=32, unique=True)),
                ('mime_type', models.CharField(blank=True, default='', max_length=100)),
                ('duration_seconds', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('latitude', models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ('longitude', models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ('address', models.CharField(blank=True, default='', max_length=255)),
                ('statement', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='media', to='Square.statement')),
            ],
            options={'ordering': ['position', 'id']},
        ),
    ]
