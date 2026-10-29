import diq.diq
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0089_chronelleaccount')]
    operations = [migrations.CreateModel(name='ChronelleCredential', fields=[
        ('id', models.CharField(max_length=64, primary_key=True, serialize=False)),
        ('sealed', models.TextField(blank=True)),
    ], options={'abstract': False, 'default_manager_name': 'objects'}, bases=(models.Model, diq.diq.Dictify))]
