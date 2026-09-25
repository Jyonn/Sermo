from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0035_linkpreview_provider_data'),
        ('Square', '0014_statementcomment_reply_to_user_alter_statement_text_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='statement',
            name='link_preview',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='square_statements',
                to='Message.linkpreview',
            ),
        ),
    ]
