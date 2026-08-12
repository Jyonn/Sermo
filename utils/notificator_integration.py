from django.utils import translation
from django.utils.translation import gettext as _

from utils.global_settings import notificator
from Config.models import Config, CI


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


def send_space_capacity_mail(space, count, limit):
    admin_email = Config.get_value_by_key(CI.ADMIN_EMAIL, default='')
    identity_tier = space.verification_tier == 'identity'
    contact_note = f' 请联系 Sermo 管理员 {admin_email} 手动调整空间规模。' if identity_tier and admin_email else ''
    return notificator.mail(
        space.email,
        format='markdown',
        title=f'{space.name} 的成员容量即将用完',
        body=f'空间当前已有 **{count}** 位成员，档位容量为 **{limit}** 人。{contact_note or "请完成更高等级认证以继续扩容。"}',
        locale='zh-CN',
        recipient_name=space_administrator_name('zh-CN'),
    )


def send_space_identity_review_mail(space):
    admin_email = Config.get_value_by_key(CI.ADMIN_EMAIL, default='')
    if not admin_email:
        return None
    return notificator.mail(
        admin_email,
        format='markdown',
        title=f'空间实名认证待审：{space.name}',
        body=f'空间 `{space.slug}` 已提交 PDF 身份凭证，请登录后续管理后台审阅。\n\n凭证 Key：`{space.identity_document_key}`',
        locale='zh-CN',
        recipient_name='Sermo 管理员',
    )


def notificator_result_detail(result):
    request_id = result.get('request_id') if isinstance(result, dict) else None
    return f'request_id:{request_id}' if request_id else None
