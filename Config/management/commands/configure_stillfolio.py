"""Set the StillFolio OAuth client credentials without printing them."""
import getpass
import re
import sys

import requests
from cryptography.fernet import Fernet, InvalidToken
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from Config.models import Config, CI
from User.stillfolio import CLIENT_ID, binding, config_cipher


class Command(BaseCommand):
    help = 'Verify and save the StillFolio OAuth secret from stdin.'

    def handle(self, *args, **options):
        secret = (getpass.getpass('StillFolio client secret: ') if sys.stdin.isatty() else sys.stdin.readline()).strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', secret):
            raise CommandError('Invalid StillFolio client secret format')
        try:
            response = requests.post(binding.api + 'introspect/', auth=(CLIENT_ID, secret),
                                     json={'token': 'configuration-check'}, timeout=(3, 8), allow_redirects=False)
            verified = response.status_code == 200 and response.json() == {'active': False}
        except (requests.RequestException, ValueError):
            verified = False
        if not verified:
            raise CommandError('StillFolio did not accept the client credentials')
        with transaction.atomic():
            cipher = config_cipher()
            storage_key = Config.get_value_by_key(CI.STILLFOLIO_STORAGE_KEY)
            if storage_key:
                try:
                    Fernet(cipher.decrypt(storage_key.encode()))
                except (InvalidToken, ValueError):
                    raise CommandError('The saved StillFolio storage key could not be read') from None
            else:
                storage_key = cipher.encrypt(Fernet.generate_key()).decode()
            Config.update_value(CI.STILLFOLIO_CLIENT_ID, CLIENT_ID)
            Config.update_value(CI.STILLFOLIO_CLIENT_SECRET, cipher.encrypt(secret.encode()).decode())
            Config.update_value(CI.STILLFOLIO_STORAGE_KEY, storage_key)
        self.stdout.write('StillFolio OAuth credentials verified and saved.')
