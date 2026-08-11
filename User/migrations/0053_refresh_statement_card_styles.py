from django.db import migrations


STYLE_REPLACEMENTS = {
    'aurora': 'default',
    'lacquer': 'default',
    'pixel': 'mosaic',
    'collage': 'hero',
}


def refresh_statement_styles(apps, schema_editor):
    User = apps.get_model('User', 'User')
    for previous, replacement in STYLE_REPLACEMENTS.items():
        User.objects.filter(statement_card_style=previous).update(statement_card_style=replacement)


class Migration(migrations.Migration):
    dependencies = [('User', '0052_user_feature_discovery')]

    operations = [migrations.RunPython(refresh_statement_styles, migrations.RunPython.noop)]
