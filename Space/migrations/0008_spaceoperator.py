from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('Space', '0007_space_admin_phone_space_admin_phone_verified_at_and_more'),
        ('User', '0070_userstateevent'),
    ]

    operations = [
        migrations.CreateModel(
            name='SpaceOperator',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='operators', to='Space.space')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='space_operator', to='User.user')),
            ],
            options={'ordering': ('created_at', 'id')},
        ),
    ]
