from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('Message', '0040_voice_call_messages')]
    operations = [migrations.CreateModel(name='SocialParseUsage', fields=[
        ('id', models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
        ('week', models.DateField()), ('count', models.PositiveSmallIntegerField(default=0)),
        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='User.user')),
    ], options={'unique_together': {('user', 'week')}})]
