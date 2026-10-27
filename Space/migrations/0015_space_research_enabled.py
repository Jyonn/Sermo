from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('Space', '0014_oauth_developer_feature_grant')]
    operations = [migrations.AddField(model_name='space', name='research_enabled', field=models.BooleanField(default=False))]
