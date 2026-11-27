from django.db import migrations, models


def fill_existing_scores(apps, schema_editor):
    Table = apps.get_model('Activity', 'LandlordTable')
    for table in Table.objects.filter(status='finished', landlord__isnull=False).prefetch_related('seats').iterator(chunk_size=250):
        base = max((bid for bid in table.bids if bid is not None), default=1) or 1
        for seat in table.seats.all():
            seat.score = base * (2 if seat.position == table.landlord else 1) * (1 if seat.won else -1)
            seat.save(update_fields=['score'])


class Migration(migrations.Migration):
    dependencies = [('Activity', '0023_landlord_activity')]

    operations = [
        migrations.AlterField(model_name='landlordtable', name='status', field=models.CharField(
            choices=[('waiting', 'Waiting'), ('bidding', 'Bidding'), ('doubling', 'Doubling'), ('playing', 'Playing'),
                     ('finished', 'Finished'), ('abandoned', 'Abandoned')], default='waiting', max_length=12)),
        migrations.AddField(model_name='landlordtable', name='multiplier', field=models.PositiveIntegerField(default=1, db_default=1)),
        migrations.AddField(model_name='landlordtable', name='spring', field=models.BooleanField(default=False, db_default=False)),
        migrations.AddField(model_name='landlordseat', name='doubled', field=models.BooleanField(default=None, null=True)),
        migrations.AddField(model_name='landlordseat', name='trustee', field=models.BooleanField(default=False, db_default=False)),
        migrations.AddField(model_name='landlordseat', name='plays', field=models.PositiveSmallIntegerField(default=0, db_default=0)),
        migrations.AddField(model_name='landlordseat', name='score', field=models.IntegerField(default=0, db_default=0)),
        migrations.RunPython(fill_existing_scores, migrations.RunPython.noop),
    ]
