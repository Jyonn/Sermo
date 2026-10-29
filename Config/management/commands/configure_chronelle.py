"""Interactive, server-only OAuth configuration. Never prints credentials."""
import re
from getpass import getpass
from cryptography.fernet import Fernet
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from Config.models import Config, CI


class Command(BaseCommand):
    help = 'Securely configure Chronelle OAuth v1 in Config (interactive stdin)'

    def handle(self, *args, **options):
        client=input('Chronelle application Key: ').strip()
        secret=getpass('Chronelle Secret (hidden): ').strip()
        if not re.fullmatch(r'coapp_[A-Za-z0-9_-]{16,80}',client) or not 32<=len(secret)<=200:
            raise CommandError('Invalid credential format; no configuration changed')
        with transaction.atomic():
            key=Config.get_value_by_key(CI.CHRONELLE_STORAGE_KEY)
            if not key:
                # Preserve an existing private-file key so sealed records remain readable.
                import os,json
                from pathlib import Path
                path=Path(os.environ.get('CHRONELLE_BINDING_CONFIG',str(Path.home()/'.config/frienden/chronelle-binding.json')))
                saved=json.loads(path.read_text()) if path.is_file() and not path.stat().st_mode & 0o077 else {}
                key=os.environ.get('CHRONELLE_BINDING_STORAGE_KEY') or saved.get('storage_key') or Fernet.generate_key().decode()
            Fernet(key.encode())
            Config.update_value(CI.CHRONELLE_CLIENT_ID,client)
            Config.update_value(CI.CHRONELLE_CLIENT_SECRET,secret)
            Config.update_value(CI.CHRONELLE_STORAGE_KEY,key)
        self.stdout.write('Chronelle OAuth configuration saved; secret values were not printed.')
