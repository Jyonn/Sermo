from django.db import migrations, models
import django.db.models.deletion


def preserve_results(apps, schema_editor):
    Table = apps.get_model('Activity', 'LandlordTable')
    Result = apps.get_model('Activity', 'LandlordRoundResult')
    for table in Table.objects.exclude(status='abandoned').prefetch_related('seats').iterator(chunk_size=250):
        seats = list(table.seats.all())
        if seats and table.host_position not in {seat.position for seat in seats}:
            table.host_position = min(seat.position for seat in seats)
            table.save(update_fields=['host_position'])
        if table.status == 'finished' and table.finished_at:
            Result.objects.bulk_create([
                Result(table=table, user_id=seat.user_id, round_number=1,
                       score=seat.score, won=seat.won, finished_at=table.finished_at)
                for seat in seats
            ])


class Migration(migrations.Migration):
    dependencies = [('Activity', '0024_landlord_scoring')]

    operations = [
        migrations.AddField(model_name='landlordtable', name='round_number', field=models.PositiveIntegerField(default=1, db_default=1)),
        migrations.AddField(model_name='landlordtable', name='host_position', field=models.PositiveSmallIntegerField(default=0, db_default=0)),
        migrations.AddField(model_name='landlordseat', name='ready', field=models.BooleanField(default=False, db_default=False)),
        migrations.CreateModel(
            name='LandlordRoundResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('round_number', models.PositiveIntegerField()),
                ('score', models.IntegerField()),
                ('won', models.BooleanField()),
                ('finished_at', models.DateTimeField(db_index=True)),
                ('table', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='round_results', to='Activity.landlordtable')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='landlord_results', to='User.user')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('table', 'round_number', 'user'), name='landlord_round_user')]},
        ),
        migrations.RunPython(preserve_results, migrations.RunPython.noop),
    ]
