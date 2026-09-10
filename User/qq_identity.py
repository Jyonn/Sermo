import datetime
import hashlib
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from User.models import QQIdentity, User, UserAccountKindChoice, UserAccountLevelChoice, UserAvatarTypeChoice
from utils import function


QQ_PATTERN = re.compile(r'^\d{5,20}$')
QZONE_EMOTICON_RE = re.compile(r'\[em\]e\d+\[/em\]', re.IGNORECASE)
QZONE_AVATAR_URL = 'https://q1.qlogo.cn/g?b=qq&nk={qq}&s=100'


def normalize_qq(qq):
    value = str(qq or '').strip()
    if not QQ_PATTERN.fullmatch(value):
        raise ValidationError('QQ must contain 5 to 20 digits.')
    return value


def _placeholder_display_name(qq, nickname):
    value = QZONE_EMOTICON_RE.sub('', str(nickname or '')).strip()
    if not value:
        value = f'QQ用户{qq[-4:]}'
    return value[:User.vldt.NAME_MAX_LENGTH]


def _qzone_avatar_uri(qq):
    return QZONE_AVATAR_URL.format(qq=qq)


def _placeholder_lower_name(qq):
    digest = hashlib.sha256(qq.encode('ascii')).hexdigest()[:17]
    return f'qz{digest}'


@transaction.atomic
def ensure_qzone_placeholder(space, qq, nickname=''):
    qq = normalize_qq(qq)
    identity = QQIdentity.objects.select_for_update().filter(space=space, qq=qq).select_related('user').first()
    if identity is not None:
        placeholder = identity.user
        if placeholder.is_imported_placeholder and placeholder.merged_into_id is None:
            display_name = _placeholder_display_name(qq, nickname)
            avatar_uri = _qzone_avatar_uri(qq)
            update_fields = []
            if placeholder.name != display_name:
                placeholder.name = display_name
                placeholder.name_pinyin = User.build_name_pinyin(display_name)
                update_fields.extend(['name', 'name_pinyin'])
            if placeholder.avatar_type != UserAvatarTypeChoice.PRESET:
                placeholder.avatar_type = UserAvatarTypeChoice.PRESET
                update_fields.append('avatar_type')
            if placeholder.avatar_uri != avatar_uri:
                placeholder.avatar_uri = avatar_uri
                update_fields.append('avatar_uri')
            if update_fields:
                placeholder.save(update_fields=update_fields)
        return identity

    display_name = _placeholder_display_name(qq, nickname)
    salt = function.get_salt(length=User.vldt.SALT_MAX_LENGTH)
    placeholder = User.objects.create(
        space=space,
        name=display_name,
        lower_name=_placeholder_lower_name(qq),
        name_pinyin=User.build_name_pinyin(display_name),
        salt=salt,
        password=None,
        account_level=UserAccountLevelChoice.BASIC,
        account_kind=UserAccountKindChoice.IMPORTED_PLACEHOLDER,
        avatar_type=UserAvatarTypeChoice.PRESET,
        avatar_uri=_qzone_avatar_uri(qq),
        welcome_message='',
        is_deleted=True,
        last_heartbeat=timezone.now() - datetime.timedelta(days=36500),
    )
    return QQIdentity.objects.create(space=space, qq=qq, user=placeholder)


@transaction.atomic
def claim_qq_identity(user, qq, verified_at=None):
    qq = normalize_qq(qq)
    target = User.objects.select_for_update().get(id=user.id)
    if target.is_deleted or target.account_kind != UserAccountKindChoice.MEMBER:
        raise ValidationError('Only an active FRIENDEN member can bind a QQ identity.')
    target.space.require_qq_binding_enabled()

    existing_for_user = QQIdentity.objects.select_for_update().filter(user=target).first()
    if existing_for_user is not None and existing_for_user.qq != qq:
        raise ValidationError('This FRIENDEN user has already bound another QQ identity.')

    identity = QQIdentity.objects.select_for_update().filter(space=target.space, qq=qq).select_related('user').first()
    claimed_at = verified_at or timezone.now()
    if identity is None:
        return QQIdentity.objects.create(
            space=target.space,
            qq=qq,
            user=target,
            verified_at=claimed_at,
        )
    if identity.user_id == target.id:
        if identity.verified_at is None:
            identity.verified_at = claimed_at
            identity.save(update_fields=['verified_at', 'updated_at'])
        return identity

    placeholder = User.objects.select_for_update().get(id=identity.user_id)
    if not placeholder.is_imported_placeholder or placeholder.merged_into_id is not None:
        raise ValidationError('This QQ identity has already been bound by another FRIENDEN user.')

    from Square.models import Statement, StatementComment, StatementCommentMention

    Statement.objects.filter(user=placeholder).update(user=target)
    StatementComment.objects.filter(user=placeholder).update(user=target)
    StatementComment.objects.filter(reply_to_user=placeholder).update(reply_to_user=target)
    old_mention_token = f'<@{placeholder.id}>'
    new_mention_token = f'<@{target.id}>'
    for mention in StatementCommentMention.objects.filter(user=placeholder).select_related('comment').iterator():
        if old_mention_token in mention.comment.text:
            mention.comment.text = mention.comment.text.replace(old_mention_token, new_mention_token)
            mention.comment.save(update_fields=['text'])
        duplicate = StatementCommentMention.objects.filter(comment_id=mention.comment_id, user=target).exists()
        if duplicate:
            mention.delete()
        else:
            mention.user = target
            mention.save(update_fields=['user'])

    placeholder.merged_into = target
    placeholder.save(update_fields=['merged_into'])
    identity.user = target
    identity.verified_at = claimed_at
    identity.save(update_fields=['user', 'verified_at', 'updated_at'])
    return identity
