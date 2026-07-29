from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('Chat', '0003_chatuserpreference'),
        ('TravelMap', '0001_initial'),
        ('User', '0036_remove_retired_chat_bubble_styles'),
    ]

    operations = [
        migrations.AddField(
            model_name='mapcheckin',
            name='accuracy_meters',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mapcheckin',
            name='geocoding_provider',
            field=models.CharField(blank=True, max_length=24, null=True),
        ),
        migrations.AddField(
            model_name='mapcheckin',
            name='latitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mapcheckin',
            name='longitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='MapChatGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('granted_at', models.DateTimeField(auto_now_add=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('chat', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='map_grants', to='Chat.chat')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_map_grants', to='User.user')),
            ],
        ),
        migrations.AddConstraint(
            model_name='mapchatgrant',
            constraint=models.UniqueConstraint(fields=('chat', 'owner'), name='travel_map_unique_chat_owner'),
        ),
    ]
