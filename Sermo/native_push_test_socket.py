"""Fail closed before an opt-in database rehearsal can open a connection."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def private_mysql_socket(value):
    socket = Path(value)
    try:
        parent = socket.parent.resolve(strict=True)
        metadata = parent.stat()
        valid = (
            socket.is_absolute()
            and socket.parent == parent
            and socket.name == 'mysql.sock'
            and parent.parent == Path('/private/tmp')
            and parent.name.startswith('frienden-native-mysql-')
            and metadata.st_uid == os.getuid()
            and not metadata.st_mode & 0o077
            and not socket.is_symlink()
            and socket.is_socket()
        )
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise ImproperlyConfigured('A private temporary Frienden MySQL socket is required')
    return str(socket)
