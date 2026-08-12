from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('Message', '0018_mediaasset'), ('Square', '0005_unify_media_metadata')]

    operations = [
        migrations.RenameField(model_name='statementmedia', old_name='media_metadata', new_name='media_asset'),
        migrations.AlterField(
            model_name='statementmedia', name='media_asset',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='statement_media_items', to='Message.mediaasset'),
        ),
        migrations.RemoveField(model_name='statementmedia', name='kind'),
        migrations.RemoveField(model_name='statementmedia', name='key'),
        migrations.RemoveField(model_name='statementmedia', name='blob_slug'),
        migrations.RemoveField(model_name='statementmedia', name='mime_type'),
        migrations.RemoveField(model_name='statementmedia', name='duration_seconds'),
        migrations.RemoveField(model_name='statementmedia', name='latitude'),
        migrations.RemoveField(model_name='statementmedia', name='longitude'),
        migrations.RemoveField(model_name='statementmedia', name='address'),
    ]
