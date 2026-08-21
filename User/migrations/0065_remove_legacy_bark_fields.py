from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0064_add_pushdeer_provider'),
    ]

    operations = [
        migrations.RemoveField(model_name='user', name='bark'),
        migrations.RemoveField(model_name='user', name='bark_verified_at'),
        migrations.RemoveField(model_name='user', name='bark_unbound_at'),
    ]
