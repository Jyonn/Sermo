from django.utils import translation
from django.utils.translation import gettext as _

from utils.global_settings import notificator


def notificator_locale(language=None):
    normalized = str(language or translation.get_language() or '').strip().lower()
    return 'en-US' if normalized.startswith('en') else 'zh-CN'


def django_language(locale):
    return 'en' if notificator_locale(locale) == 'en-US' else 'zh_CN'


def verification_title(kind, language=None):
    locale = notificator_locale(language)
    with translation.override(django_language(locale)):
        if kind == 'password_recovery':
            return str(_('Sermo password recovery'))
        if kind == 'space':
            return str(_('Sermo space verification code'))
        return str(_('Sermo verification code'))


def verification_message_text(code, time, language=None):
    locale = notificator_locale(language)
    with translation.override(django_language(locale)):
        return str(_('Your verification code is {code}. It expires in {time} minutes.').format(
            code=code,
            time=time,
        ))


def space_administrator_name(language=None):
    locale = notificator_locale(language)
    with translation.override(django_language(locale)):
        return str(_('Space administrator'))


def send_verification_mail(target, code, time, title, language=None, recipient_name=None):
    locale = notificator_locale(language)
    return notificator.mail(
        target,
        format='verification',
        title=title,
        locale=locale,
        body=dict(code=str(code), time=int(time)),
        recipient_name=recipient_name,
    )


def send_verification_sms(target, code, time, title, language=None):
    locale = notificator_locale(language)
    return notificator.sms(
        target,
        format='verification',
        title=title,
        locale=locale,
        body=dict(code=str(code), time=int(time)),
    )


def notificator_result_detail(result):
    request_id = result.get('request_id') if isinstance(result, dict) else None
    return f'request_id:{request_id}' if request_id else None
