import os
from pathlib import Path
import socket
import tempfile
from unittest import skipUnless

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from Sermo.native_push_test_socket import private_mysql_socket


@skipUnless(Path('/private/tmp').is_dir(), 'macOS private temporary socket harness')
class PrivateMySQLSocketTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='frienden-native-mysql-', dir='/private/tmp')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'mysql.sock'
        self.server = socket.socket(socket.AF_UNIX)
        self.addCleanup(self.server.close)
        self.server.bind(str(self.path))

    def test_private_local_socket_is_accepted(self):
        self.assertEqual(private_mysql_socket(str(self.path)), str(self.path))

    def test_missing_parent_is_a_configuration_error(self):
        with self.assertRaises(ImproperlyConfigured):
            private_mysql_socket(str(self.path.parent / 'missing' / 'mysql.sock'))

    def test_relative_path_and_remote_host_are_rejected(self):
        for value in ('mysql.sock', 'localhost', '127.0.0.1', '/tmp/mysql.sock'):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                private_mysql_socket(value)

    def test_shared_directory_is_rejected(self):
        os.chmod(self.path.parent, 0o755)
        self.addCleanup(os.chmod, self.path.parent, 0o700)
        with self.assertRaises(ImproperlyConfigured):
            private_mysql_socket(str(self.path))

    def test_regular_file_is_not_a_socket(self):
        self.server.close()
        self.path.unlink()
        self.path.touch()
        with self.assertRaises(ImproperlyConfigured):
            private_mysql_socket(str(self.path))

    def test_socket_symlink_is_rejected(self):
        original = self.path.with_name('original.sock')
        self.path.rename(original)
        self.path.symlink_to(original)
        with self.assertRaises(ImproperlyConfigured):
            private_mysql_socket(str(self.path))
