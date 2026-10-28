import diq.diq
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('Chat', '0022_researchmeeting'), ('User', '0089_chronelleaccount')]
    operations = [migrations.CreateModel(name='ResearchCalendarPreference', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('default_busy', models.BooleanField(default=True)),
        ('overrides', models.JSONField(default=dict)),
        ('binding_subject', models.CharField(blank=True, max_length=128)),
        ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='research_calendar_preference', to='User.user')),
    ], options={'abstract':False,'default_manager_name':'objects'}, bases=(models.Model,diq.diq.Dictify))]
