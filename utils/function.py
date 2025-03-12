import hashlib

from django.utils.crypto import get_random_string


def get_salt(length):
    return get_random_string(length or 32)


def hash_password(password, salt: str):
    return hashlib.sha256((password + salt).encode()).hexdigest()


def verify_password(password, salt, key):
    return key == hash_password(password, salt) if password else True


def indent(string, indent='\t'):
    lines = string.split('\n')
    return '\n'.join([indent + line for line in lines])
