from django.contrib.auth.password_validation import CommonPasswordValidator
from django.core.exceptions import ValidationError

from User.validators import UserErrors


def password_policy(user):
    return dict(min_length=10 if user.is_official else 6, max_length=32 if user.is_official else 128,
                required_classes=3 if user.is_official else 0, official=user.is_official)


def validate_new_password(user, password):
    policy = password_policy(user)
    if not isinstance(password, str) or not policy['min_length'] <= len(password) <= policy['max_length']:
        if user.is_official:
            raise UserErrors.OFFICIAL_PASSWORD_WEAK
        raise UserErrors.PASSWORD_LENGTH_INVALID
    if user.is_official:
        classes = sum((any(c.islower() for c in password), any(c.isupper() for c in password),
                       any(c.isdigit() for c in password), any(not c.isalnum() and not c.isspace() for c in password)))
        if classes < policy['required_classes'] or any(ord(c) < 32 or ord(c) == 127 for c in password):
            raise UserErrors.OFFICIAL_PASSWORD_WEAK
        try:
            CommonPasswordValidator().validate(password)
        except ValidationError:
            raise UserErrors.OFFICIAL_PASSWORD_WEAK
