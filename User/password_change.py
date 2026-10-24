from django.db import transaction
from django.utils import timezone

from User.models import User, UserNotificationChoice, UserPasswordRecoveryChallenge, RefreshToken
from User.password_policy import validate_new_password
from User.validators import UserErrors
from utils import function


def change_password(user, new_password, method='password', old_password=None, challenge_id=None, code=None):
    validate_new_password(user, new_password)
    if method in ('email', 'sms'):
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
