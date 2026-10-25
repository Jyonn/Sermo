from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('Message', '0035_linkpreview_provider_data'), ('User', '0001_initial')]
    operations = [migrations.CreateModel(
        name='MessageCopy',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('message', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='copies', to='Message.message')),
            ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='message_copies', to='User.user')),
        ],
        options={'constraints': [models.UniqueConstraint(fields=('message', 'user'), name='message_copy_user_unique')]},
    )]
