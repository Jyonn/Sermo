"""Synthetic-only MySQL backup/restore rehearsal; refuses non-/tmp databases.

Run with DJANGO_SETTINGS_MODULE=sermo_oauth_mysql_settings and PYTHONPATH=/tmp:.
The setting must point at the disposable Unix socket prepared for this task.
Never accepts deployment settings, remote servers, or copied credentials.
"""
import hashlib
import os
from datetime import timedelta
from pathlib import Path
import subprocess

import django
import pymysql

if os.environ.get('DJANGO_SETTINGS_MODULE') != 'sermo_oauth_mysql_settings':
    raise SystemExit('Refusing settings other than the synthetic rehearsal settings')
django.setup()

from django.core.management import call_command
from django.db import connection
from django.utils import timezone
from Space.models import Space
from User.models import OAuthApplication, OAuthGrant, User
from User.management.commands.seed_oauth_applications import seed
from User.oauth_registry import mutate

config = connection.settings_dict
socket = config['HOST']
if not socket.startswith('/tmp/sermo-oauth-mysql.') or not socket.endswith('/mysql.sock') or config['NAME'] != 'oauth_rehearsal' or config['USER'] != 'root' or config['PASSWORD']:
    raise SystemExit('Refusing non-synthetic database/socket/credentials')
root = Path(socket).parent
mysql = ['/opt/homebrew/bin/mysql', '--no-defaults', f'--socket={socket}', '-u', 'root']
dump = ['/opt/homebrew/bin/mysqldump', '--no-defaults', f'--socket={socket}', '-u', 'root', '--single-transaction', '--skip-lock-tables', '--set-gtid-purged=OFF', '--no-tablespaces', '--skip-comments', 'oauth_rehearsal']

with connection.cursor() as cursor:
    cursor.execute('SHOW TABLES')
    if cursor.fetchall():
        raise SystemExit('Refusing a nonempty rehearsal database; use a fresh synthetic database')

def raw(name):
    return pymysql.connect(unix_socket=socket, user='root', database=name, charset='utf8mb4')

def rows(database, table):
    with raw(database) as conn:
        with conn.cursor() as cursor:
            cursor.execute(f'SELECT * FROM `{table}` ORDER BY id')
            return cursor.fetchall()

def backup_and_restore(label, tables):
    destination = root / f'{label}.sql'
    snapshot = {table: rows('oauth_rehearsal', table) for table in tables}
    with destination.open('wb') as output:
        subprocess.run(dump, stdout=output, check=True)
    restore_name = f'oauth_restore_{label}'
    subprocess.run([*mysql, '-e', f'CREATE DATABASE `{restore_name}` CHARACTER SET utf8mb4'], check=True)
    with destination.open('rb') as content:
        subprocess.run([*mysql, restore_name], stdin=content, check=True)
    for table in tables:
        if rows(restore_name, table) != snapshot[table]:
            raise AssertionError(f'Restored synthetic {table} differs')
    print(f'PASS {label}: SQL backup restored into separate local database; {len(tables)} tables compared exactly')
    return restore_name

call_command('migrate', interactive=False, verbosity=0)
call_command('migrate', 'User', '0082', interactive=False, verbosity=0)
space = Space.objects.create(name='Synthetic backup', slug='backup-test', email='synthetic@example.com')
user = User.objects.create(space=space, name='Synthetic')
records = [
    ('stillfolio', 'https://stillfolio.jyonn.space/auth/sermo/callback', ['phone', 'profile', 'vip']),
    ('chronelle', 'https://chronelle.jyonn.space/api/profile/sermo/callback/', ['profile']),
    ('supervision-local', 'http://127.0.0.1:8001/api/auth/friendom/callback/', ['profile']),
]
for client_id, uri, scopes in records:
    OAuthGrant.objects.create(user=user, client_id=client_id, redirect_uri=uri, scopes=scopes, code_challenge='c' * 43,
                              code_hash=hashlib.sha256(f'{client_id}:synthetic-code'.encode()).hexdigest(),
                              code_expires_at=timezone.now() + timedelta(minutes=5),
                              access_token_hash=hashlib.sha256(f'{client_id}:synthetic-token'.encode()).hexdigest(),
                              access_expires_at=timezone.now() + timedelta(hours=1))
before_grants = rows('oauth_rehearsal', 'User_oauthgrant')
before_name = backup_and_restore('before_registry', ['User_oauthgrant', 'User_user', 'Space_space', 'django_migrations'])
with raw(before_name) as conn:
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE 'User_oauthapplication%'")
        assert not cursor.fetchall(), 'Pre-registry restore must not contain registry tables'

call_command('migrate', 'User', '0083', interactive=False, verbosity=0)
seed(records[0][1])
app = OAuthApplication.objects.get(client_id='supervision-local')
mutate(actor='synthetic-admin@example.com', admin=True, app_id=app.id, data=dict(action='disable', version=app.version))
custom = mutate(actor='synthetic-admin@example.com', admin=True,
                data=dict(action='create', client_id='synthetic-custom', name='Synthetic custom app', redirect_uris=['https://example.com/cb'], scopes=['profile']))
mutate(actor='synthetic-admin@example.com', admin=True, app_id=custom['id'], data=dict(action='disable', version=custom['version']))
assert rows('oauth_rehearsal', 'User_oauthgrant') == before_grants
after_name = backup_and_restore('after_registry', ['User_oauthgrant', 'User_user', 'Space_space', 'django_migrations', 'User_oauthapplication', 'User_oauthapplicationrevision', 'User_oauthapplicationaudit'])
with raw(after_name) as conn:
    with conn.cursor() as cursor:
        cursor.execute("SELECT enabled FROM User_oauthapplication WHERE client_id IN ('supervision-local','synthetic-custom') ORDER BY client_id")
        assert cursor.fetchall() == ((0,), (0,)), 'Restored policy must retain disabled applications'
print('PASS: three legacy grants unchanged; restored registry, revisions, audits and disable policy retained')
print('Synthetic dumps remain under the disposable /tmp MySQL directory; no production data was accessed')
