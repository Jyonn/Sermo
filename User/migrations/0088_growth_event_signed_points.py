from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0087_self_avatar_default_enabled')]
    operations = [migrations.AlterField(model_name='growthevent', name='points', field=models.IntegerField(default=0))]
