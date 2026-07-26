from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0027_user_growth_profile_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserPasswordRecoveryChallenge',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel', models.IntegerField(choices=[(0, 0), (1, 1), (2, 2), (3, 3)], db_index=True)),
                ('target', models.CharField(max_length=255)),
                ('code', models.CharField(max_length=6)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('reset_token', models.CharField(blank=True, max_length=96, null=True, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('code_expires_at', models.DateTimeField(db_index=True)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('reset_expires_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='password_recovery_challenges', to='User.user')),
            ],
            options={
                'default_manager_name': 'objects',
            },
        ),
    ]
