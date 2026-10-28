from django.db import migrations, models


def simplify_statuses(apps, schema_editor):
    Project = apps.get_model('Chat', 'ResearchProject')
    for project in Project.objects.using(schema_editor.connection.alias).all().iterator():
        project.status = 'archived' if project.status in ('completed', 'archived') else 'active'
        project.tasks = [dict(task, status='archived' if task.get('status') in ('done', 'archived') else 'active') for task in project.tasks]
        project.revision += 1
        project.save(update_fields=['status', 'tasks', 'revision'])


class Migration(migrations.Migration):
    dependencies = [('Chat', '0023_researchcalendarpreference')]
    operations = [
        migrations.AlterField(model_name='researchproject', name='status', field=models.CharField(default='active', max_length=16)),
        migrations.RunPython(simplify_statuses, migrations.RunPython.noop),
    ]
