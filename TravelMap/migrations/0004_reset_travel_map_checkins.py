from django.db import migrations


def reset_checkins(apps, schema_editor):
    MapCheckIn = apps.get_model('TravelMap', 'MapCheckIn')
    MapCheckIn.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('TravelMap', '0003_repair_china_region_codes'),
    ]

    operations = [
        migrations.RunPython(reset_checkins, migrations.RunPython.noop),
    ]
