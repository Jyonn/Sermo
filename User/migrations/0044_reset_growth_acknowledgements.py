from django.db import migrations


def reset_growth_acknowledgements(apps, schema_editor):
    User = apps.get_model('User', 'User')
    User.objects.exclude(growth_acknowledged_level=0).update(growth_acknowledged_level=0)


class Migration(migrations.Migration):
    dependencies = [('User', '0043_rebuild_growth_v1')]

    operations = [
        migrations.RunPython(reset_growth_acknowledgements, migrations.RunPython.noop),
    ]
