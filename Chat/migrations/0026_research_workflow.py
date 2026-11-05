from django.db import migrations, models
import django.db.models.deletion
import diq.diq


class Migration(migrations.Migration):
    dependencies = [('Chat', '0025_research_resources'), ('Message', '0039_mediaasset_transcode_safety')]
    operations = [
        migrations.AddField(model_name='researchproject', name='workflow', field=models.JSONField(default=dict)),
        migrations.CreateModel(name='ResearchEvidence', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('reimbursement_id', models.CharField(max_length=64)),
            ('media', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='research_evidence', to='Message.mediaresource')),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='evidence', to='Chat.researchproject')),
        ], options={'abstract': False, 'default_manager_name': 'objects'}, bases=(models.Model, diq.diq.Dictify)),
    ]
