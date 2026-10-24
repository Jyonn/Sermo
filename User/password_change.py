from django.db import transaction
from django.utils import timezone

from User.models import User, UserNotificationChoice, UserPasswordRecoveryChallenge, RefreshToken
from User.password_policy import validate_new_password
from User.validators import UserErrors
from utils import function


def verify_password_change(user, method='password', old_password=None, challenge_id=None, code=None):
    if method in ('email', 'sms'):
        channel = UserNotificationChoice.EMAIL if method == 'email' else UserNotificationChoice.SMS
        challenge = UserPasswordRecoveryChallenge.verify_code(challenge_id, code, user_id=user.id, channel=channel)
        return dict(reset_token=challenge.reset_token)
    if method != 'password':
        raise UserErrors.PASSWORD_RECOVERY_CHANNEL_INVALID
    if user.has_password:
        if not old_password:
            raise UserErrors.OLD_PASSWORD_REQUIRED
        if not function.verify_password(old_password, user.salt, user.password):
            raise UserErrors.PASSWORD_ERROR
    return dict(verified=True)


def change_password(user, new_password, method='password', old_password=None, challenge_id=None, code=None, reset_token=None):
    validate_new_password(user, new_password)
    if method in ('email', 'sms'):
        if reset_token:
            return UserPasswordRecoveryChallenge.reset_password(reset_token, new_password, user_id=user.id)
        channel = UserNotificationChoice.EMAIL if method == 'email' else UserNotificationChoice.SMS
        # Verification commits failed attempts independently from password mutation.
        challenge = UserPasswordRecoveryChallenge.verify_code(challenge_id, code, user_id=user.id, channel=channel)
        return UserPasswordRecoveryChallenge.reset_password(challenge.reset_token, new_password, user_id=user.id)
    if method != 'password':
        raise UserErrors.PASSWORD_RECOVERY_CHANNEL_INVALID
    with transaction.atomic():
        current = User.objects.select_for_update().get(pk=user.id)
        if current.has_password:
            if not old_password:
                raise UserErrors.OLD_PASSWORD_REQUIRED
            if not function.verify_password(old_password, current.salt, current.password):
                raise UserErrors.PASSWORD_ERROR
        current.set_password(new_password)
        now = timezone.now()
        RefreshToken.objects.filter(user=current, revoked_at__isnull=True).update(revoked_at=now)
        UserPasswordRecoveryChallenge.objects.filter(user=current, used_at__isnull=True).update(used_at=now)
        return current
