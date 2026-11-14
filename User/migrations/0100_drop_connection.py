from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('User', '0099_chat_quick_actions')]
    operations = [migrations.CreateModel(name='DropConnection', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('sealed', models.TextField(blank=True)), ('pending', models.TextField(blank=True)),
        ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='User.user')),
    ], options={'default_manager_name': 'objects'})]
