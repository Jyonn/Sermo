import diq.diq
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('User','0088_growth_event_signed_points')]
    operations=[migrations.CreateModel(name='ChronelleAccount',fields=[
        ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
        ('sealed',models.TextField(blank=True)),
        ('user',models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,related_name='chronelle_account',to='User.user')),
    ],options={'abstract':False,'default_manager_name':'objects'},bases=(models.Model,diq.diq.Dictify))]
