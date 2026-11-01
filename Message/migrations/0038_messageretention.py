from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('Message', '0037_backfill_remaining_welcome_templates')]
    operations = [migrations.CreateModel(
        name='MessageRetention',
        fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('message', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='retention_marks', to='Message.message')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='User.user'))],
        options={'constraints': [models.UniqueConstraint(fields=('user', 'message'), name='message_retention_user_unique')]},
    )]
