import hashlib
import string

from django.utils.crypto import get_random_string


def get_salt(length):
    return get_random_string(length or 32)


def get_subdomain(length):
    return get_random_string(length, allowed_chars=string.ascii_lowercase + string.digits)


def hash_password(password, salt: str):
    return hashlib.sha256((password + salt).encode()).hexdigest()


def verify_password(password, salt, key):
    if not password:
        return False
    return key == hash_password(password, salt)
