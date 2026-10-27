import uuid
import diq.diq
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('Chat','0021_research_calendar_policy'),('User','0089_chronelleaccount')]
    operations=[migrations.CreateModel(name='ResearchMeeting',fields=[
        ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False,serialize=False)),
        ('payload',models.JSONField()),('status',models.CharField(max_length=16,default='pending')),
        ('invitation_id',models.UUIDField(null=True,blank=True)),('lease_until',models.DateTimeField(null=True,blank=True)),
        ('created_at',models.DateTimeField(auto_now_add=True)),
        ('creator',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='+',to='User.user')),
        ('project',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='meetings',to='Chat.researchproject')),
    ],options={'abstract':False,'default_manager_name':'objects'},bases=(models.Model,diq.diq.Dictify))]
