"""OAuth-provider eligibility only; never used for normal login or app management."""

VERIFICATION_PATH = '/app/menu?panel=account-security'


def eligibility(user):
    missing = [channel for channel in ('email', 'phone')
               if not (getattr(user, channel, None) or '').strip()
               or getattr(user, f'{channel}_verified_at', None) is None]
    return {'eligible': not user.is_deleted and not missing,
            'missing_verifications': missing, 'verification_path': VERIFICATION_PATH}
