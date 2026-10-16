from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0080_oauthgrant')]
    operations = [migrations.AddField(
        model_name='user', name='chronelle_calendar_id',
        field=models.CharField(max_length=36, default='', blank=True),
    )]
