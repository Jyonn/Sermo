from django.db import migrations


CANONICAL_WEB_ORIGIN = 'https://sermo.jyonn.space'


def disable_legacy_subdomain_web_push(apps, schema_editor):
    WebPushSubscription = apps.get_model('User', 'WebPushSubscription')
    WebPushSubscription.objects.filter(
        enabled=True,
        origin__iendswith='.sermo.jyonn.space',
    ).exclude(origin__iexact=CANONICAL_WEB_ORIGIN).update(enabled=False)


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0053_refresh_statement_card_styles'),
    ]

    operations = [
        migrations.RunPython(disable_legacy_subdomain_web_push, migrations.RunPython.noop),
    ]
