import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0096_pocklo_connection')]
    operations = [
        migrations.CreateModel(
            name='StillfolioCredential',
            fields=[
                ('phone_digest', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('sealed', models.TextField()),
            ],
            options={'default_manager_name': 'objects'},
        ),
        migrations.CreateModel(
            name='StillfolioConnection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone_digest', models.CharField(blank=True, max_length=64)),
                ('pending', models.TextField(blank=True)),
                ('credential', models.ForeignKey(null=True, on_delete=models.deletion.SET_NULL, to='User.stillfoliocredential')),
                ('user', models.OneToOneField(on_delete=models.deletion.CASCADE, to='User.user')),
            ],
            options={'default_manager_name': 'objects'},
        ),
    ]
