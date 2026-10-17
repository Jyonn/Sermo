from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0083_oauth_application_registry')]
    operations = [migrations.AddField(
        model_name='oauthapplicationrevision', name='icon_url',
        field=models.URLField(blank=True, default='', max_length=512),
    )]
