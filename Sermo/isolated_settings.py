"""Explicit opt-in, temporary SQLite-only tests; never imports deployment settings."""
import sys

# Legacy modules import Sermo.settings directly. Keep those imports isolated too.
sys.modules['Sermo.settings'] = sys.modules[__name__]
SECRET_KEY = 'isolated-oauth-tests-only-not-for-deployment'
PROJ_INIT = True
DEBUG = False
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
ALLOWED_HOSTS = ['testserver', 'localhost']
ROOT_URLCONF = 'Sermo.urls'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
    'django.contrib.messages', 'django.contrib.staticfiles', 'corsheaders', 'Config',
    'Space', 'User', 'Friendship', 'TravelMap', 'Chat', 'Message', 'Sticker', 'Square',
    'QZone', 'PlatformAdmin', 'AccessPolicy', 'Activity',
]
MIDDLEWARE = ['utils.middleware.APIPacker']
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
STATIC_URL = '/static/'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'APP_DIRS': True}]
