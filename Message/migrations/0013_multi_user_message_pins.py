from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0012_pinnedmessage'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pinnedmessage',
            name='message',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='pins',
                to='Message.message',
            ),
        ),
        migrations.AddConstraint(
            model_name='pinnedmessage',
            constraint=models.UniqueConstraint(
                fields=('message', 'pinned_by'),
                name='unique_message_pin_user',
            ),
        ),
    ]
