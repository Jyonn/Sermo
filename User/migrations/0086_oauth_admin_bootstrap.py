from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('User', '0085_space_bound_oauth')]
    operations = [migrations.AlterField(model_name='oauthauthorizationrequest', name='space',
        field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='Space.space'))]
