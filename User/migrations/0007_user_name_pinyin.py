import re

from django.db import migrations, models
from pypinyin import lazy_pinyin


HANZI_PATTERN = re.compile(r'[\u4e00-\u9fff]')


def _is_hanzi(char: str):
    return bool(char and HANZI_PATTERN.fullmatch(char))


def _is_letter(char: str):
    if not char:
        return False
    lower = char.lower()
    return 'a' <= lower <= 'z'


def build_name_pinyin(name: str):
    normalized = (name or '').strip()
    if not normalized:
        return ''

    first = normalized[0]
    if not (_is_hanzi(first) or _is_letter(first)):
        return ''

    filtered = [char for char in normalized if _is_hanzi(char) or _is_letter(char)]
    if not filtered:
        return ''

    result = []
    for char in filtered:
        if _is_letter(char):
            result.append(char.lower())
        else:
            result.extend(lazy_pinyin(char))
    return ''.join(result).lower()


def fill_name_pinyin(apps, schema_editor):
    User = apps.get_model('User', 'User')
    for user in User.objects.all().only('id', 'name').iterator():
        user.name_pinyin = build_name_pinyin(user.name)
        user.save(update_fields=['name_pinyin'])


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0006_user_login_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='name_pinyin',
            field=models.CharField(db_index=True, default='', max_length=255),
        ),
        migrations.RunPython(fill_name_pinyin, migrations.RunPython.noop),
    ]
