from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0026_fix_image_capture_timezone'),
        ('Square', '0008_statement_location'),
    ]

    operations = [
        migrations.AddField(
            model_name='statement',
            name='forward_bundle',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='square_statements', to='Message.forwardbundle',
            ),
        ),
    ]
