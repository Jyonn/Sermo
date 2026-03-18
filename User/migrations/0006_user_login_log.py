from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Space', '0002_space_official_user'),
        ('User', '0005_user_avatar'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserLoginLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.GenericIPAddressField(blank=True, null=True)),
                ('logged_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('space', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='login_logs', to='Space.space')),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='login_logs', to='User.user')),
            ],
        ),
    ]
