from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0007_user_name_pinyin'),
    ]

    operations = [
        migrations.DeleteModel(
            name='EmailVerificationCode',
        ),
    ]

