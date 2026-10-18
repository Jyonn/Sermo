"""Secret-free SQLite test settings; never import the deployment settings."""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = 'isolated-fixture-key-not-for-deployment'
PROJ_INIT = True
DEBUG = False
ALLOWED_HOSTS = ['testserver', 'localhost']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
LANGUAGE_CODE = 'en'
ROOT_URLCONF = 'Sermo.urls'
MIDDLEWARE = ['utils.middleware.APIPacker']
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'Config', 'Space', 'User', 'Friendship', 'TravelMap', 'Chat', 'Message', 'Sticker', 'Square', 'QZone', 'PlatformAdmin', 'AccessPolicy', 'Activity']
OAUTH_SPACE_REQUESTS_ENABLED = True
# Legacy modules import these two constants by the deployment module name.
# Alias only inside this explicitly selected test configuration.
sys.modules['Sermo.settings'] = sys.modules[__name__]
