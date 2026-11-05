from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('User', '0094_web_push_health_receipts')]
    operations = [
        migrations.AddField(model_name='user', name='birthday_month', field=models.PositiveSmallIntegerField(null=True, blank=True)),
        migrations.AddField(model_name='user', name='birthday_day', field=models.PositiveSmallIntegerField(null=True, blank=True)),
        migrations.AddField(model_name='user', name='birthday_set_at', field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name='user', name='birthday_welcomed_year', field=models.PositiveSmallIntegerField(null=True, blank=True)),
        migrations.CreateModel(name='BirthdayLetter', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('birthday_year', models.PositiveSmallIntegerField()),
            ('occasion_date', models.DateField()),
            ('source', models.CharField(max_length=16, choices=[('platform', 'platform'), ('space', 'space')])),
            ('sender_name', models.CharField(max_length=100)), ('written_by', models.CharField(max_length=254)),
            ('title', models.CharField(max_length=80)), ('body', models.TextField()),
            ('opened_at', models.DateTimeField(null=True, blank=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='birthday_letters', to='User.user')),
        ], options={'constraints': [models.UniqueConstraint(fields=('user', 'birthday_year', 'source'), name='unique_birthday_letter_source')]}),
    ]
