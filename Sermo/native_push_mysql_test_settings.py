"""Opt-in local socket tests; never import deployment settings or credentials."""
import os
import pymysql

from .isolated_test_settings import *
from .native_push_test_socket import private_mysql_socket

socket = private_mysql_socket(os.environ.get('FRIENDEN_MYSQL_TEST_SOCKET', '/missing-test-socket'))

pymysql.install_as_MySQLdb()
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.mysql',
    'NAME': 'frienden_native_push_validation',
    'USER': 'root',
    'PASSWORD': '',
    'HOST': str(socket),
    'OPTIONS': {'charset': 'utf8mb4', 'isolation_level': 'read committed',
                'init_command': "SET sql_mode='STRICT_TRANS_TABLES', innodb_lock_wait_timeout=5"},
    'TEST': {'NAME': 'test_frienden_native_push_validation', 'CHARSET': 'utf8mb4'},
}}
