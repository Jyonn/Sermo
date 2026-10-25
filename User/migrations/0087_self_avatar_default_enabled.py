from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0086_oauth_admin_bootstrap')]
    operations = [migrations.AlterField(
        model_name='user', name='show_self_avatar',
        field=models.BooleanField(default=True),
    )]
