from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Space', '0012_space_space_group_enabled')]
    operations = [migrations.AddField(model_name='space', name='oauth_developer_enabled', field=models.BooleanField(default=False))]
