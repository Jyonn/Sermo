from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0033_permanent_vip_campaign'),
    ]

    operations = [
        migrations.AddField(model_name='user', name='chat_bubble_style', field=models.CharField(default='default', max_length=16)),
        migrations.AddField(model_name='user', name='avatar_frame_style', field=models.CharField(default='none', max_length=16)),
        migrations.AddField(model_name='user', name='square_outfit_style', field=models.CharField(default='sunset', max_length=16)),
        migrations.AddField(model_name='user', name='square_prop_style', field=models.CharField(default='none', max_length=16)),
        migrations.AddField(model_name='user', name='square_motion_style', field=models.CharField(default='walk', max_length=16)),
        migrations.AddField(model_name='user', name='square_limb_style', field=models.CharField(default='line', max_length=16)),
    ]
