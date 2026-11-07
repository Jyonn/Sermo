import gettext

from django.conf import settings
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from User.models import BirthdayLetter, NativePushBinding, NativePushDelivery, NotificationDelivery, User, WebPushSubscription


class NativePushCompatibilityTests(TestCase):
    def test_compiled_chinese_catalog_keeps_native_error_translations(self):
        with (settings.BASE_DIR / 'locale/zh_CN/LC_MESSAGES/django.mo').open('rb') as source:
            catalog = gettext.GNUTranslations(source)
        for message in ('Invalid native push request', 'Native push settings changed; refresh before retrying'):
            translated = catalog.gettext(message)
            self.assertNotEqual(translated, message)
            self.assertTrue(any(ord(character) > 127 for character in translated))

    def test_native_and_current_user_migrations_share_one_forward_path(self):
        loader = MigrationLoader(connection)
        leaves = loader.graph.leaf_nodes('User')
        self.assertEqual(len(leaves), 1)
        path = loader.graph.forwards_plan(leaves[0])
        for name in ('0094_native_push_bindings', '0095_native_push_delivery_queue',
                     '0094_web_push_health_receipts', '0095_birthday', '0096_merge_native_push'):
            self.assertIn(('User', name), path)
        merge = loader.disk_migrations[('User', '0096_merge_native_push')]
        self.assertEqual(merge.operations, [])

    def test_applied_schema_preserves_native_browser_and_birthday_columns(self):
        expected = {
            NativePushBinding: ('installation_id', 'environment', 'opted_in', 'revision'),
            NativePushDelivery: ('binding_id', 'event_id', 'claim_id'),
            WebPushSubscription: ('invalidated_at',),
            NotificationDelivery: ('client_received_at', 'notification_submitted_at'),
            User: ('birthday_month', 'birthday_day', 'birthday_set_at'),
            BirthdayLetter: ('birthday_year', 'occasion_date', 'opened_at'),
        }
        with connection.cursor() as cursor:
            for model, names in expected.items():
                columns = {column.name for column in connection.introspection.get_table_description(cursor, model._meta.db_table)}
                self.assertTrue(set(names).issubset(columns), model.__name__)
