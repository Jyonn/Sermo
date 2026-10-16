from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0081_user_chronelle_calendar')]
    operations = [migrations.AddField(
        model_name='user', name='chronelle_authorization',
        field=models.JSONField(default=dict, blank=True),
    )]
