from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('Square', '0006_simplify_statementmedia'),
        ('User', '0068_user_profile_card_theme'),
    ]

    operations = [
        migrations.CreateModel(
            name='SquareReadState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('explore_statement_id', models.PositiveBigIntegerField(default=0)),
                ('friends_statement_id', models.PositiveBigIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='square_read_state', to='User.user')),
            ],
            options={
                'default_manager_name': 'objects',
            },
        ),
    ]
