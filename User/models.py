import datetime
import hashlib
import ipaddress
import logging
import math
import re
import threading
from collections import Counter

from notificator import NotificatorAPIError
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import F, Q
from django.utils import timezone, translation
from django.utils.crypto import get_random_string
from django.utils.translation import gettext as _
from pypinyin import lazy_pinyin
from smartdjango import models, Choice

from utils.global_settings import notificator
from utils.qiniu import (
    sign_private_download_url,
    delete_avatar_by_uri,
    build_avatar_display_uri,
    delete_chat_background_by_uri,
)
from User.validators import UserValidator, UserErrors
from User.growth import (
    CHAT_BACKGROUND_LEVELS,
    DAILY_GROWTH_LIMIT,
    EVENT_RULES,
    GROWTH_CAPABILITY_LEVELS,
    GROWTH_THRESHOLDS,
    LEVEL_REWARDS,
    PERSONALIZATION_LEVELS,
    VIP_OR_LEVEL_PERSONALIZATION,
    WEEKLY_GROWTH_LIMIT,
    level_unlock_titles,
    resolve_event_rule,
)
from utils import function


FRONTEND_BASE_URL = 'https://sermo.jyonn.space'
BARK_ENDPOINT_PATTERN = re.compile(r'^https://api\.day\.app/([^/?#\s]+)', re.IGNORECASE)
logger = logging.getLogger(__name__)

def _is_emoji_base(char):
    code = ord(char)
    return (
        0x1F000 <= code <= 0x1FAFF
        or 0x2600 <= code <= 0x27BF
        or 0x1F1E6 <= code <= 0x1F1FF
        or code in (0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299)
    )


def extract_emojis(text):
    chars = list(text or '')
    result = []
    index = 0
    while index < len(chars):
        if not _is_emoji_base(chars[index]):
            index += 1
            continue
        token = chars[index]
        index += 1
        if 0x1F1E6 <= ord(token) <= 0x1F1FF and index < len(chars) and 0x1F1E6 <= ord(chars[index]) <= 0x1F1FF:
            token += chars[index]
            index += 1
        while index < len(chars):
            code = ord(chars[index])
            if code in (0xFE0E, 0xFE0F, 0x20E3) or 0x1F3FB <= code <= 0x1F3FF:
                token += chars[index]
                index += 1
                continue
            if code == 0x200D and index + 1 < len(chars) and _is_emoji_base(chars[index + 1]):
                token += chars[index] + chars[index + 1]
                index += 2
                continue
            break
        result.append(token)
    return result


def extract_emoji_counts(text):
    return Counter(extract_emojis(text))


def account_switch_phone_variants(value):
    phone = (value or '').strip()
    if not phone:
        return set()
    if phone.startswith('+86') and len(phone) > 3:
        return {phone, phone[3:]}
    if phone.startswith('86') and len(phone) > 2:
        return {phone, f'+{phone}', phone[2:]}
    if phone.startswith('+'):
        return {phone}
    return {phone, f'+86{phone}'}


def normalize_bark_endpoint(value):
    target = (value or '').strip()
    matched = BARK_ENDPOINT_PATTERN.match(target)
    if matched is None:
        return target
    return f'https://api.day.app/{matched.group(1)}'


class UserNotificationChoice(Choice):
    UNSET = 0
    EMAIL = 1
    SMS = 2
    BARK = 3


class UserAccountLevelChoice(Choice):
    BASIC = 0
    VERIFIED = 1


class UserRoleChoice(Choice):
    OFFICIAL = 0
    MEMBER = 1


class UserAvatarTypeChoice(Choice):
    PRESET = 'preset'
    CUSTOM = 'custom'


class UserNormalizers:
    @staticmethod
    def name(value):
        return (value or '').strip()

    @staticmethod
    def lower_name(value):
        return UserNormalizers.name(value).lower()

    @staticmethod
    def language(value):
        return UserValidator.normalize_language(value)

    @staticmethod
    def welcome_message(value):
        return (value or '').strip()


class User(models.Model):
    normalizers = UserNormalizers
    validators = UserValidator
    vldt = UserValidator
    MEMBER_WELCOME_MESSAGE_ZH = '我已同意你的好友申请，快来和我聊天吧～'
    MEMBER_WELCOME_MESSAGE_EN = 'I accepted your friend request. Come chat with me!'
    OFFICIAL_WELCOME_MESSAGE_ZH = '欢迎加入{space}！'
    OFFICIAL_WELCOME_MESSAGE_EN = 'Welcome to {space}!'
    DEFAULT_PLAZA_GREETING_ZH = '嗨，认识一下？'
    DEFAULT_PLAZA_GREETING_EN = 'Hi, nice to meet you.'
    AVATAR_PRESET_BASE_URI = 'https://image.6-79.cn/sermo/assets/avatars'
    HANZI_PATTERN = re.compile(r'[\u4e00-\u9fff]')

    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='users', db_index=True)

    name = models.CharField(max_length=vldt.NAME_MAX_LENGTH, validators=[vldt.name])
    lower_name = models.CharField(max_length=vldt.NAME_MAX_LENGTH, db_index=True)
    name_pinyin = models.CharField(max_length=255, default='', db_index=True)

    password = models.CharField(
        max_length=vldt.PASSWORD_MAX_LENGTH,
        null=True,
        blank=True,
        validators=[vldt.password],
    )
    account_level = models.IntegerField(
        choices=UserAccountLevelChoice.to_choices(),
        default=UserAccountLevelChoice.BASIC,
    )
    role = models.IntegerField(
        choices=UserRoleChoice.to_choices(),
        default=UserRoleChoice.MEMBER,
        db_index=True,
    )
    language = models.CharField(
        max_length=vldt.LANGUAGE_MAX_LENGTH,
        default=vldt.DEFAULT_LANGUAGE,
        validators=[vldt.language],
    )
    language_preference = models.CharField(
        max_length=vldt.LANGUAGE_MAX_LENGTH,
        default='system',
        validators=[vldt.language_preference],
    )

    offline_notification_interval = models.PositiveIntegerField(
        default=vldt.OFFLINE_MIN_INTERVAL,
        validators=[vldt.offline_notification_interval],
    )
    notification_channel = models.IntegerField(
        choices=UserNotificationChoice.to_choices(),
        default=UserNotificationChoice.UNSET,
    )

    is_online = models.BooleanField(default=False)
    last_heartbeat = models.DateTimeField(auto_now=True)

    email = models.EmailField(null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    bark = models.CharField(max_length=100, null=True, blank=True)
    bark_verified_at = models.DateTimeField(null=True, blank=True)
    email_unbound_at = models.DateTimeField(null=True, blank=True)
    phone_unbound_at = models.DateTimeField(null=True, blank=True)
    bark_unbound_at = models.DateTimeField(null=True, blank=True)
    is_private_account = models.BooleanField(default=False)
    welcome_message = models.CharField(
        max_length=vldt.WELCOME_MESSAGE_MAX_LENGTH,
        default='',
        validators=[vldt.welcome_message],
    )
    plaza_greeting = models.CharField(max_length=vldt.PLAZA_GREETING_MAX_LENGTH, default='')
    name_changed_at = models.DateTimeField(null=True, blank=True)
    avatar_type = models.CharField(
        max_length=16,
        choices=UserAvatarTypeChoice.to_choices(),
        default=UserAvatarTypeChoice.PRESET,
    )
    avatar_uri = models.CharField(
        max_length=255,
        default='',
    )
    growth_score = models.PositiveIntegerField(default=0)
    growth_level = models.PositiveSmallIntegerField(default=1)
    growth_acknowledged_level = models.PositiveSmallIntegerField(default=0)
    is_permanent_vip = models.BooleanField(default=False, db_index=True)
    chat_background_theme = models.CharField(max_length=16, default='default')
    chat_background_uri = models.CharField(max_length=255, default='')
    chat_bubble_style = models.CharField(max_length=16, default='default')
    avatar_frame_style = models.CharField(max_length=16, default='none')
    square_outfit_style = models.CharField(max_length=16, default='sunset')
    square_prop_style = models.CharField(max_length=16, default='none')
    square_motion_style = models.CharField(max_length=16, default='walk')
    square_limb_style = models.CharField(max_length=16, default='line')

    created_at = models.DateTimeField(auto_now_add=True)
    salt = models.CharField(max_length=vldt.SALT_MAX_LENGTH)

    is_deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = ('space', 'lower_name')

    @classmethod
    def index(cls, user_id):
        try:
            return cls.objects.get(id=user_id, is_deleted=False)
        except cls.DoesNotExist:
            raise UserErrors.NOT_EXISTS(attr=_('user id'), value=user_id)

    @classmethod
    def index_any(cls, user_id):
        try:
            return cls.objects.get(id=user_id)
        except cls.DoesNotExist:
            raise UserErrors.NOT_EXISTS(attr=_('user id'), value=user_id)

    @classmethod
    def jwt_login(cls, user_id):
        return cls.index(user_id)

    @classmethod
    def _assert_name_available(cls, space, name):
        lower_name = name.lower()
        if cls.objects.filter(space=space, lower_name=lower_name, is_deleted=False).exists():
            raise UserErrors.EXISTS

    @classmethod
    def _is_hanzi(cls, char: str):
        return bool(char and cls.HANZI_PATTERN.fullmatch(char))

    @staticmethod
    def _is_letter(char: str):
        if not char:
            return False
        lower = char.lower()
        return 'a' <= lower <= 'z'

    @classmethod
    def build_name_pinyin(cls, name: str):
        normalized = (name or '').strip()
        if not normalized:
            return ''

        first = normalized[0]
        if not (cls._is_hanzi(first) or cls._is_letter(first)):
            return ''

        filtered = [char for char in normalized if cls._is_hanzi(char) or cls._is_letter(char)]
        if not filtered:
            return ''

        result = []
        for char in filtered:
            if cls._is_letter(char):
                result.append(char.lower())
            else:
                result.extend(lazy_pinyin(char))
        return ''.join(result).lower()

    @classmethod
    def _normalize_email(cls, email: str):
        return (email or '').strip().lower()

    @classmethod
    def _deleted_lower_name(cls, user_id: int):
        value = f'd{int(user_id)}'
        return value[-cls.vldt.NAME_MAX_LENGTH:]

    @classmethod
    def build_preset_avatar_uri(cls, preset_id: int):
        validated = cls.vldt.avatar_preset_id(preset_id)
        return f'{cls.AVATAR_PRESET_BASE_URI}/{validated:02d}.svg'

    @classmethod
    def _default_avatar_preset_id(cls, salt: str):
        span = cls.vldt.AVATAR_PRESET_MAX_ID - cls.vldt.AVATAR_PRESET_MIN_ID + 1
        return (sum(ord(c) for c in (salt or '')) % span) + cls.vldt.AVATAR_PRESET_MIN_ID

    @classmethod
    def create(
            cls,
            space,
            name,
            password=None,
            role: int = UserRoleChoice.MEMBER,
            email: str = None,
            verified: bool = False,
            language: str = None,
    ):
        name = name.strip()
        cls.vldt.name(name)
        cls._assert_name_available(space, name)

        salt = function.get_salt(length=cls.vldt.SALT_MAX_LENGTH)
        if role == UserRoleChoice.OFFICIAL:
            verified = True
        normalized_language = cls.vldt.language(language)
        welcome_message = cls.default_welcome_message(
            space=space,
            role=role,
            language=normalized_language,
        )
        default_avatar_preset_id = cls._default_avatar_preset_id(salt)
        normalized_email = cls._normalize_email(email) or None
        email_verified_at = timezone.now() if verified and normalized_email else None
        user = cls.objects.create(
            space=space,
            name=name,
            lower_name=name.lower(),
            name_pinyin=cls.build_name_pinyin(name),
            salt=salt,
            role=role,
            language=normalized_language,
            welcome_message=welcome_message,
            email=normalized_email,
            email_verified_at=email_verified_at,
            account_level=UserAccountLevelChoice.VERIFIED if verified else UserAccountLevelChoice.BASIC,
            avatar_type=UserAvatarTypeChoice.PRESET,
            avatar_uri=cls.build_preset_avatar_uri(default_avatar_preset_id),
            is_private_account=role == UserRoleChoice.OFFICIAL,
        )
        if password:
            user.set_password(password)
        cls._ensure_official_friendship(user)
        return user

    @classmethod
    def login(cls, space, name, password, language=None):
        name = (name or '').strip()
        lower_name = name.lower()
        normalized_language = cls.vldt.language(language)
        user = cls.objects.filter(space=space, lower_name=lower_name, is_deleted=False).first()
        if user is None:
            deleted_user = cls.objects.filter(space=space, lower_name=lower_name, is_deleted=True).first()
            if deleted_user is not None:
                deleted_user.release_deleted_identity()
            space.ensure_member_limit_available()
            return cls.create(
                space=space,
                name=name,
                password=password,
                language=normalized_language,
            )

        if user.is_deleted:
            raise UserErrors.USER_DELETED
        if user.password:
            if not password:
                raise UserErrors.PASSWORD_REQUIRED
            if not function.verify_password(password, user.salt, user.password):
                raise UserErrors.PASSWORD_ERROR
            user.set_language(normalized_language)
            user.ensure_welcome_message(language=normalized_language)
            cls._ensure_official_friendship(user)
            return user

        if password:
            user.set_password(password)
        user.set_language(normalized_language)
        user.ensure_welcome_message(language=normalized_language)
        cls._ensure_official_friendship(user)
        return user

    @classmethod
    def _ensure_official_friendship(cls, user):
        if user.role == UserRoleChoice.OFFICIAL:
            return
        official = user.space.official_user or user.space.ensure_official_user()
        from Friendship.models import Friendship, FriendshipStatusChoice

        relation = Friendship.between(user, official)
        should_send_welcome = relation is None or relation.status != FriendshipStatusChoice.ACCEPTED

        Friendship.ensure_locked_friendship(user, official)
        if should_send_welcome:
            Friendship.send_welcome_message(sender=official, receiver=user)

    def set_password(self, password, save=True):
        if not password:
            return self
        self.password = function.hash_password(password, self.salt)
        if save:
            self.save(update_fields=['password'])
            self.award_growth('security:password')
        return self

    def set_language(self, language, save=True):
        normalized = self.vldt.language(language)
        if self.language_preference != 'system':
            normalized = self.vldt.language(self.language_preference)
        if self.language == normalized:
            return self
        self.language = normalized
        if save:
            self.save(update_fields=['language'])
        return self

    def set_language_preference(self, preference, system_language=None, save=True):
        normalized_preference = self.vldt.language_preference(preference)
        effective_language = (
            self.vldt.language(system_language)
            if normalized_preference == 'system'
            else self.vldt.language(normalized_preference)
        )
        self.language_preference = normalized_preference
        self.language = effective_language
        if save:
            self.save(update_fields=['language_preference', 'language'])
        return self

    def set_name(self, name, save=True):
        self.require_growth_capability('rename_nickname')
        available_at = self.nickname_change_available_at()
        if available_at and timezone.now() < available_at:
            raise UserErrors.NICKNAME_CHANGE_COOLDOWN(available_at=available_at.isoformat())
        normalized = (name or '').strip()
        self.vldt.name(normalized)
        lower_name = normalized.lower()
        if lower_name != self.lower_name:
            if User.objects.filter(space=self.space, lower_name=lower_name, is_deleted=False).exclude(id=self.id).exists():
                raise UserErrors.EXISTS

        self.name = normalized
        self.lower_name = lower_name
        self.name_pinyin = self.build_name_pinyin(normalized)
        if save:
            self.name_changed_at = timezone.now()
            self.save(update_fields=['name', 'lower_name', 'name_pinyin', 'name_changed_at'])
        return self

    def effective_growth_level(self):
        score = GROWTH_THRESHOLDS[-1] if self.is_official else self.growth_score
        return min(self._growth_level_for_score(score), self.growth_level_cap()[0])

    def has_growth_capability(self, capability):
        if capability == 'custom_notification_message' and self.is_permanent_vip:
            return True
        return self.effective_growth_level() >= GROWTH_CAPABILITY_LEVELS[capability]

    def require_growth_capability(self, capability):
        required_level = GROWTH_CAPABILITY_LEVELS[capability]
        if not self.has_growth_capability(capability):
            raise UserErrors.GROWTH_LEVEL_REQUIRED(level=required_level)
        return self

    def set_chat_background(self, theme, uri=''):
        normalized_theme = self.validators.chat_background_theme(theme)
        required_level = CHAT_BACKGROUND_LEVELS[normalized_theme]
        if normalized_theme != self.chat_background_theme and self.effective_growth_level() < required_level:
            raise UserErrors.GROWTH_LEVEL_REQUIRED(level=required_level)
        normalized_uri = (uri or '').strip() if normalized_theme == 'custom' else ''
        previous_uri = self.chat_background_uri
        self.chat_background_theme = normalized_theme
        self.chat_background_uri = normalized_uri
        self.save(update_fields=['chat_background_theme', 'chat_background_uri'])
        if normalized_theme == 'custom' and normalized_uri:
            self.award_growth('explore:custom_background')
        if previous_uri and previous_uri != normalized_uri:
            delete_chat_background_by_uri(previous_uri)
        return self

    def set_personalization(self, **values):
        fields = ['chat_bubble_style', 'avatar_frame_style']
        changed = any(values[field] != getattr(self, field) for field in fields)
        if values['chat_bubble_style'] == 'vip' and not self.is_permanent_vip:
            raise UserErrors.PERMANENT_VIP_NOT_ELIGIBLE
        if values['avatar_frame_style'] == 'vip' and not self.is_permanent_vip:
            raise UserErrors.PERMANENT_VIP_NOT_ELIGIBLE
        for field in fields:
            normalized = self.validators.personalization(field, values[field])
            if field in PERSONALIZATION_LEVELS and normalized in PERSONALIZATION_LEVELS[field] and normalized != getattr(self, field):
                required_level = PERSONALIZATION_LEVELS[field][normalized]
                vip_override = (field, normalized) in VIP_OR_LEVEL_PERSONALIZATION and self.is_permanent_vip
                if not vip_override and self.effective_growth_level() < required_level:
                    raise UserErrors.GROWTH_LEVEL_REQUIRED(level=required_level)
            setattr(self, field, normalized)
        self.save(update_fields=fields)
        if changed:
            self.award_growth('explore:first_personalization')
        return self

    def nickname_change_interval_days(self):
        level = self.effective_growth_level()
        if level < 5:
            return None
        if level == 5:
            return 365
        if level < 8:
            return 365
        if level < 12:
            return 30
        return 7

    def nickname_change_available_at(self):
        days = self.nickname_change_interval_days()
        if days is None or self.name_changed_at is None:
            return None
        return self.name_changed_at + datetime.timedelta(days=days)

    @classmethod
    def default_welcome_message(cls, space, role, language):
        normalized_language = cls.vldt.normalize_language(language)
        if role == UserRoleChoice.OFFICIAL:
            if normalized_language == 'en':
                return cls.OFFICIAL_WELCOME_MESSAGE_EN.format(space=space.name)
            return cls.OFFICIAL_WELCOME_MESSAGE_ZH.format(space=space.name)
        if normalized_language == 'en':
            return cls.MEMBER_WELCOME_MESSAGE_EN
        return cls.MEMBER_WELCOME_MESSAGE_ZH

    def ensure_welcome_message(self, language=None, save=True):
        if (self.welcome_message or '').strip():
            return self
        self.welcome_message = self.default_welcome_message(
            space=self.space,
            role=self.role,
            language=language or self.language,
        )
        if save:
            self.save(update_fields=['welcome_message'])
            self.award_growth('explore:welcome')
        return self

    def display_plaza_greeting(self):
        greeting = (self.plaza_greeting or '').strip()
        if greeting:
            return greeting
        return (
            self.DEFAULT_PLAZA_GREETING_ZH
            if self.vldt.normalize_language(self.language) == 'zh-CN'
            else self.DEFAULT_PLAZA_GREETING_EN
        )

    def set_welcome_message(self, welcome_message, save=True):
        self.require_growth_capability('welcome_message')
        normalized = self.vldt.welcome_message(welcome_message)
        self.welcome_message = normalized
        if save:
            self.save(update_fields=['welcome_message'])
        return self

    def set_plaza_greeting(self, plaza_greeting, save=True):
        self.require_growth_capability('plaza_greeting')
        self.plaza_greeting = self.vldt.plaza_greeting(plaza_greeting)
        if save:
            self.save(update_fields=['plaza_greeting'])
        return self

    def bind_contact(self, channel: int, target: str):
        target = (target or '').strip()
        now = timezone.now()

        if channel == UserNotificationChoice.EMAIL:
            normalized = self._normalize_email(target)
            with transaction.atomic():
                from Space.models import Space
                Space.objects.select_for_update().get(id=self.space_id)
                if not self.is_official and User.objects.filter(
                    space_id=self.space_id,
                    role=UserRoleChoice.MEMBER,
                    is_deleted=False,
                    email=normalized,
                ).exclude(id=self.id).exists():
                    raise UserErrors.CONTACT_ALREADY_BOUND
                self.email = normalized
                self.email_verified_at = now
                self.account_level = UserAccountLevelChoice.VERIFIED
                self.save(update_fields=['email', 'email_verified_at', 'account_level'])
            self.award_growth('security:email')
            return self
        if channel == UserNotificationChoice.SMS:
            with transaction.atomic():
                from Space.models import Space
                Space.objects.select_for_update().get(id=self.space_id)
                if not self.is_official and User.objects.filter(
                    space_id=self.space_id,
                    role=UserRoleChoice.MEMBER,
                    is_deleted=False,
                    phone=target,
                ).exclude(id=self.id).exists():
                    raise UserErrors.CONTACT_ALREADY_BOUND
                self.phone = target
                self.phone_verified_at = now
                self.save(update_fields=['phone', 'phone_verified_at'])
            self.award_growth('security:phone')
            return self
        if channel == UserNotificationChoice.BARK:
            self.bark = normalize_bark_endpoint(target)
            self.bark_verified_at = now
            self.save(update_fields=['bark', 'bark_verified_at'])
            self.award_growth('security:bark')
            return self
        raise UserErrors.CONTACT_CHANNEL_INVALID

    def set_private_account(self, enabled: bool):
        if enabled and not (
            self.phone
            and self.phone_verified_at is not None
        ):
            raise UserErrors.PRIVATE_ACCOUNT_CONTACTS_REQUIRED
        self.is_private_account = bool(enabled)
        self.save(update_fields=['is_private_account'])
        return self

    def unbind_contact(self, channel: int, verification=None):
        now = timezone.now()
        if channel == UserNotificationChoice.EMAIL:
            if not self.email or self.email_verified_at is None:
                raise UserErrors.CONTACT_NOT_BOUND
            if verification is None or verification.target != self.email:
                raise UserErrors.CONTACT_UNBIND_TARGET_MISMATCH
            available_at = self.email_unbound_at + datetime.timedelta(days=30) if self.email_unbound_at else None
            if available_at and available_at > now:
                raise UserErrors.CONTACT_UNBIND_TOO_FREQUENT(available_at=available_at.isoformat())
            self.email = None
            self.email_verified_at = None
            self.email_unbound_at = now
            if not self.is_official:
                self.account_level = UserAccountLevelChoice.BASIC
                self.is_private_account = False
            fields = ['email', 'email_verified_at', 'email_unbound_at', 'account_level', 'is_private_account']
        elif channel == UserNotificationChoice.SMS:
            if not self.phone or self.phone_verified_at is None:
                raise UserErrors.CONTACT_NOT_BOUND
            if verification is None or verification.target != self.phone:
                raise UserErrors.CONTACT_UNBIND_TARGET_MISMATCH
            available_at = self.phone_unbound_at + datetime.timedelta(days=365) if self.phone_unbound_at else None
            if available_at and available_at > now:
                raise UserErrors.CONTACT_UNBIND_TOO_FREQUENT(available_at=available_at.isoformat())
            self.phone = None
            self.phone_verified_at = None
            self.phone_unbound_at = now
            if not self.is_official:
                self.is_private_account = False
            fields = ['phone', 'phone_verified_at', 'phone_unbound_at', 'is_private_account']
        elif channel == UserNotificationChoice.BARK:
            if not self.bark or self.bark_verified_at is None:
                raise UserErrors.CONTACT_NOT_BOUND
            self.bark = None
            self.bark_verified_at = None
            self.bark_unbound_at = now
            fields = ['bark', 'bark_verified_at', 'bark_unbound_at']
        else:
            raise UserErrors.CONTACT_CHANNEL_INVALID

        self.save(update_fields=fields)
        NotificationPreference.set_preference(user=self, channel=channel, enabled=False)
        return self

    def set_preset_avatar(self, preset_id: int, save=True):
        previous_avatar_type = self.avatar_type
        previous_avatar_uri = self.avatar_uri
        self.avatar_type = UserAvatarTypeChoice.PRESET
        self.avatar_uri = self.build_preset_avatar_uri(preset_id)
        if save:
            self.save(update_fields=['avatar_type', 'avatar_uri'])
            self._delete_previous_custom_avatar(previous_avatar_type, previous_avatar_uri, self.avatar_uri)
            self.award_growth('explore:avatar')
        return self

    def set_custom_avatar(self, avatar_uri: str, save=True):
        self.require_growth_capability('custom_avatar')
        previous_avatar_type = self.avatar_type
        previous_avatar_uri = self.avatar_uri
        self.avatar_type = UserAvatarTypeChoice.CUSTOM
        self.avatar_uri = (avatar_uri or '').strip()
        if save:
            self.save(update_fields=['avatar_type', 'avatar_uri'])
            self._delete_previous_custom_avatar(previous_avatar_type, previous_avatar_uri, self.avatar_uri)
            self.award_growth('explore:avatar')
        return self

    @staticmethod
    def _delete_previous_custom_avatar(previous_avatar_type, previous_avatar_uri, current_avatar_uri):
        old_uri = (previous_avatar_uri or '').strip()
        if previous_avatar_type != UserAvatarTypeChoice.CUSTOM or not old_uri:
            return None
        if old_uri == (current_avatar_uri or '').strip():
            return None
        return delete_avatar_by_uri(old_uri)

    @property
    def verified(self):
        return self.account_level == UserAccountLevelChoice.VERIFIED

    @property
    def is_official(self):
        return self.role == UserRoleChoice.OFFICIAL

    @property
    def has_password(self):
        return bool((self.password or '').strip())

    def heartbeat(self):
        was_alive = self.is_alive
        self.last_heartbeat = timezone.now()
        self.save(update_fields=['last_heartbeat'])
        if not was_alive:
            from Chat.models import ChatUserPreference
            ChatUserPreference.emit_peer_online_events(self)

    def release_deleted_identity(self, save=True):
        if not self.is_deleted:
            return self
        released_lower_name = self._deleted_lower_name(self.id)
        if self.lower_name == released_lower_name:
            return self
        self.lower_name = released_lower_name
        if save:
            self.save(update_fields=['lower_name'])
        return self

    def _cleanup_relations_for_removal(self):
        from Friendship.models import Friendship, FriendshipStatusChoice
        from Chat.models import ChatMember, ChatMemberStatusChoice

        current_time = timezone.now()

        Friendship.objects.filter(
            space=self.space,
        ).filter(
            Q(user_low=self) | Q(user_high=self),
        ).exclude(
            status=FriendshipStatusChoice.DELETED,
        ).update(
            status=FriendshipStatusChoice.DELETED,
            responded_at=current_time,
            updated_at=current_time,
        )

        ChatMember.objects.filter(
            user=self,
            status=ChatMemberStatusChoice.ACTIVE,
        ).update(
            status=ChatMemberStatusChoice.LEFT,
            left_at=current_time,
            updated_at=current_time,
        )

        ChatMember.objects.filter(
            user=self,
            status=ChatMemberStatusChoice.PENDING,
        ).update(
            status=ChatMemberStatusChoice.REJECTED,
            left_at=current_time,
            updated_at=current_time,
        )

    def has_removal_residue(self):
        from Friendship.models import Friendship, FriendshipStatusChoice
        from Chat.models import ChatMember, ChatMemberStatusChoice

        if Friendship.objects.filter(
            space=self.space,
        ).filter(
            Q(user_low=self) | Q(user_high=self),
        ).exclude(
            status=FriendshipStatusChoice.DELETED,
        ).exists():
            return True

        return ChatMember.objects.filter(
            user=self,
            status__in=(ChatMemberStatusChoice.ACTIVE, ChatMemberStatusChoice.PENDING),
        ).exists()

    def remove(self):
        if self.role == UserRoleChoice.OFFICIAL:
            raise UserErrors.USER_OFFICIAL_REMOVE_FORBIDDEN
        with transaction.atomic():
            self._cleanup_relations_for_removal()
            self.is_deleted = True
            self.lower_name = self._deleted_lower_name(self.id)
            self.save(update_fields=['is_deleted', 'lower_name'])
        return self

    def log_login(self, ip: str = None):
        return UserLoginLog.create_for_user(self, ip=ip)

    @property
    def is_alive(self):
        current_time = timezone.now()
        return (current_time - self.last_heartbeat).total_seconds() < self.vldt.OFFLINE_MIN_INTERVAL * 60

    def _dictify_user_id(self):
        return self.id

    def _dictify_last_heartbeat(self):
        return self.last_heartbeat.timestamp()

    def _dictify_email_verified_at(self):
        if self.email_verified_at is None:
            return None
        return self.email_verified_at.timestamp()

    def _dictify_phone_verified_at(self):
        if self.phone_verified_at is None:
            return None
        return self.phone_verified_at.timestamp()

    def _dictify_bark_verified_at(self):
        if self.bark_verified_at is None:
            return None
        return self.bark_verified_at.timestamp()

    def _dictify_avatar_uri(self):
        avatar_uri = (self.avatar_uri or '').strip()
        if not avatar_uri:
            return avatar_uri
        if self.avatar_type == UserAvatarTypeChoice.CUSTOM:
            return build_avatar_display_uri(avatar_uri)
        return avatar_uri

    def _dictify_official(self):
        return self.is_official

    def _dictify_has_password(self):
        return self.has_password

    def _dictify_growth_level_name(self):
        names = self.space.level_names or []
        index = max(0, min(len(names) - 1, self.growth_level - 1))
        return names[index] if names else ''

    def award_growth(self, key, points=None, category=None, title=None, daily_limit=None):
        rule = resolve_event_rule(key)
        if self.is_official or rule is None:
            return 0
        event_key = key
        with transaction.atomic():
            locked = User.objects.select_for_update().get(id=self.id)
            event, created = GrowthEvent.objects.get_or_create(
                user=locked,
                event_key=event_key,
                defaults=dict(category=rule.category, title=rule.title, points=0),
            )
            if not created and event.points > 0:
                return 0
            awarded = rule.points
            if rule.period == 'daily':
                period_key = event_key.split(':')[2]
                earned = GrowthEvent.period_points(locked, 'daily', period_key)
                awarded = min(awarded, max(0, DAILY_GROWTH_LIMIT - earned))
            elif rule.period == 'weekly':
                period_key = next((part for part in event_key.split(':') if re.fullmatch(r'\d{4}-W\d{2}', part)), '')
                earned = GrowthEvent.period_points(locked, 'weekly', period_key)
                awarded = min(awarded, max(0, WEEKLY_GROWTH_LIMIT - earned))
            if awarded <= 0:
                return 0
            event.category = rule.category
            event.title = rule.title
            event.points = awarded
            event.save(update_fields=['category', 'title', 'points'])
            locked.growth_score += awarded
            locked.growth_level = min(
                locked._growth_level_for_score(locked.growth_score),
                locked.growth_level_cap()[0],
            )
            locked.save(update_fields=['growth_score', 'growth_level'])
            self.growth_score = locked.growth_score
            self.growth_level = locked.growth_level
        return awarded

    def reconcile_growth(self):
        if self.is_official:
            return GROWTH_THRESHOLDS[-1]
        with transaction.atomic():
            locked = User.objects.select_for_update().get(id=self.id)
            events = list(locked.growth_events.select_for_update().order_by('created_at', 'id'))
            total = 0
            daily_totals = {}
            weekly_totals = {}
            for event in events:
                rule = resolve_event_rule(event.event_key)
                if rule is None:
                    event.delete()
                    continue
                normalized_points = rule.points
                if rule.period == 'daily':
                    period_key = event.event_key.split(':')[2]
                    available = max(0, DAILY_GROWTH_LIMIT - daily_totals.get(period_key, 0))
                    normalized_points = min(normalized_points, available)
                    daily_totals[period_key] = daily_totals.get(period_key, 0) + normalized_points
                elif rule.period == 'weekly':
                    period_key = next((part for part in event.event_key.split(':') if re.fullmatch(r'\d{4}-W\d{2}', part)), '')
                    available = max(0, WEEKLY_GROWTH_LIMIT - weekly_totals.get(period_key, 0))
                    normalized_points = min(normalized_points, available)
                    weekly_totals[period_key] = weekly_totals.get(period_key, 0) + normalized_points
                changed_fields = []
                for field, value in (('category', rule.category), ('title', rule.title), ('points', normalized_points)):
                    if getattr(event, field) != value:
                        setattr(event, field, value)
                        changed_fields.append(field)
                if changed_fields:
                    event.save(update_fields=changed_fields)
                total += normalized_points
            level = min(locked._growth_level_for_score(total), locked.growth_level_cap()[0])
            if locked.growth_score != total or locked.growth_level != level:
                locked.growth_score = total
                locked.growth_level = level
                locked.save(update_fields=['growth_score', 'growth_level'])
            self.growth_score = total
            self.growth_level = level
        return total

    @staticmethod
    def _growth_level_for_score(score):
        return max(index + 1 for index, threshold in enumerate(GROWTH_THRESHOLDS) if score >= threshold)

    def growth_level_cap(self):
        if not self.has_password:
            return 3, '设置密码后继续升级'
        if self.email_verified_at is None:
            return 6, '认证邮箱后继续升级'
        if self.phone_verified_at is None:
            return 9, '绑定手机后继续升级'
        return 18, ''

    def calculate_growth(self, save=True):
        if self.is_official:
            score = GROWTH_THRESHOLDS[-1]
        else:
            score = self.reconcile_growth()
        level_cap, level_cap_reason = self.growth_level_cap()
        score_level = self._growth_level_for_score(score)
        level = min(score_level, level_cap)
        if save and (self.growth_score != score or self.growth_level != level):
            self.growth_score = score
            self.growth_level = level
            self.save(update_fields=['growth_score', 'growth_level'])
        names = self.space.level_names or []
        next_score = GROWTH_THRESHOLDS[level] if level < len(GROWTH_THRESHOLDS) else None
        current_threshold = GROWTH_THRESHOLDS[level - 1]
        progress = 1 if next_score is None else (score - current_threshold) / max(1, next_score - current_threshold)
        privileges = [title for unlock_level in range(1, level + 1) for title in level_unlock_titles(unlock_level)]
        recent_events = list(self.growth_events.order_by('-updated_at')[:8])
        earned_keys = set(self.growth_events.values_list('event_key', flat=True))
        return dict(
            score=score,
            score_level=score_level,
            effective_level=level,
            level=level,
            acknowledged_level=min(self.growth_acknowledged_level, level),
            pending_level=(
                self.growth_acknowledged_level + 1
                if self.growth_acknowledged_level < level
                else None
            ),
            name=names[level - 1] if len(names) >= level else f'Lv.{level}',
            next_score=next_score,
            progress=round(max(0, min(1, progress)), 4),
            privileges=privileges,
            level_cap=level_cap,
            level_cap_reason=level_cap_reason,
            recent_events=[event.jsonl() for event in recent_events],
            daily=dict(
                earned=GrowthEvent.period_points(self, 'daily', timezone.localdate().isoformat()),
                limit=DAILY_GROWTH_LIMIT,
            ),
            weekly=dict(
                earned=GrowthEvent.period_points(self, 'weekly', timezone.localdate().strftime('%G-W%V')),
                limit=WEEKLY_GROWTH_LIMIT,
            ),
            milestones=[
                dict(key=rule.key, category=rule.category, title=rule.title, points=rule.points, earned=self.is_official or rule.key in earned_keys)
                for rule in EVENT_RULES.values()
                if rule.category in {'explore', 'security'}
            ],
            levels=[
                dict(
                    level=index,
                    name=names[index - 1] if len(names) >= index else f'Lv.{index}',
                    score=threshold,
                    unlocks=level_unlock_titles(index),
                    rewards=LEVEL_REWARDS.get(index, []),
                    unlocked=level >= index,
                )
                for index, threshold in enumerate(GROWTH_THRESHOLDS, start=1)
            ],
            capabilities={
                key: dict(
                    required_level=required_level,
                    available=(
                        level >= required_level
                        or (key == 'custom_notification_message' and self.is_permanent_vip)
                    ),
                )
                for key, required_level in GROWTH_CAPABILITY_LEVELS.items()
            },
        )

    def acknowledge_growth_level(self, level):
        self.calculate_growth()
        with transaction.atomic():
            locked = User.objects.select_for_update().get(id=self.id)
            expected_level = locked.growth_acknowledged_level + 1
            if level != expected_level or level > locked.growth_level:
                raise UserErrors.GROWTH_ACKNOWLEDGEMENT_INVALID
            locked.growth_acknowledged_level = level
            locked.save(update_fields=['growth_acknowledged_level'])
            self.growth_acknowledged_level = level
        return self.calculate_growth(save=False)

    def tiny_json(self):
        return self.dictify(
            'name', 'user_id', 'official', 'avatar_type', 'avatar_uri', 'is_permanent_vip',
            'chat_bubble_style', 'avatar_frame_style', 'growth_level',
        )

    def jsonl(self):
        payload = self.dictify(
            'name',
            'user_id',
            'official',
            'verified',
            'is_alive',
            'welcome_message',
            'plaza_greeting',
            'growth_level',
            'is_permanent_vip',
            'chat_bubble_style',
            'avatar_frame_style',
            'avatar_type',
            'avatar_uri',
        )
        payload['plaza_greeting'] = self.display_plaza_greeting()
        return payload

    def json_friend(self):
        return self.dictify(
            'name',
            'name_pinyin',
            'user_id',
            'official',
            'verified',
            'is_alive',
            'is_permanent_vip',
            'avatar_frame_style',
            'avatar_type',
            'avatar_uri',
            'last_heartbeat',
        )

    def jwt_json(self):
        return self.dictify('name', 'user_id', 'space_id', 'language', 'verified')

    def json(self):
        return self.jsonl()

    def json_admin(self):
        data = self.jsonl()
        data['is_deleted'] = bool(self.is_deleted)
        data['has_removal_residue'] = self.has_removal_residue() if self.is_deleted else False
        return data

    def json_me(self):
        payload = self.dictify(
            'name',
            'user_id',
            'official',
            'has_password',
            'language',
            'language_preference',
            'welcome_message',
            'plaza_greeting',
            'is_alive',
            'verified',
            'avatar_type',
            'avatar_uri',
            'email',
            'phone',
            'bark',
            'last_heartbeat',
            'email_verified_at',
            'phone_verified_at',
            'bark_verified_at',
            'email_unbound_at',
            'phone_unbound_at',
            'bark_unbound_at',
            'is_private_account',
            'is_permanent_vip',
            'chat_background_theme',
            'chat_bubble_style',
            'avatar_frame_style',
        )
        payload['chat_background_uri'] = (
            sign_private_download_url(self.chat_background_uri)
            if self.chat_background_uri
            else ''
        )
        payload['growth'] = self.calculate_growth()
        payload['permanent_vip_campaign'] = PermanentVipCampaign.status_for(self)
        payload['plaza_greeting'] = self.display_plaza_greeting()
        payload['name_changed_at'] = self.name_changed_at.timestamp() if self.name_changed_at else None
        available_at = self.nickname_change_available_at()
        payload['nickname_change'] = dict(
            interval_days=self.nickname_change_interval_days(),
            available_at=available_at.timestamp() if available_at else None,
        )
        return payload


class GrowthEvent(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='growth_events',
    )
    event_key = models.CharField(max_length=100)
    category = models.CharField(max_length=20)
    title = models.CharField(max_length=40)
    points = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'event_key'],
                name='user_growth_event_unique',
            ),
        ]

    @classmethod
    def period_points(cls, user, prefix, period_key):
        return sum(
            cls.objects.filter(user=user, event_key__startswith=f'{prefix}:', event_key__contains=period_key)
            .values_list('points', flat=True)
        )

    def jsonl(self):
        return dict(
            key=self.event_key.split(':20', 1)[0],
            category=self.category,
            title=self.title,
            points=self.points,
            created_at=self.updated_at.timestamp(),
        )


class PermanentVipCampaign(models.Model):
    LIMIT = 100

    key = models.CharField(max_length=32, primary_key=True, default='founding-100')
    claimed_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def status_for(cls, user):
        campaign, _ = cls.objects.get_or_create(key='founding-100')
        claim = PermanentVipClaim.objects.filter(user=user).first()
        level = user.effective_growth_level()
        requirements = dict(
            email=bool(user.email_verified_at),
            phone=bool(user.phone_verified_at),
            level=level >= 6,
        )
        return dict(
            limit=cls.LIMIT,
            claimed=campaign.claimed_count,
            remaining=max(0, cls.LIMIT - campaign.claimed_count),
            eligible=all(requirements.values()),
            requirements=requirements,
            required_level=6,
            claimed_by_user=claim is not None,
            slot=claim.slot if claim else None,
            active=claim is None and campaign.claimed_count < cls.LIMIT,
        )

    @classmethod
    def claim_for(cls, user):
        with transaction.atomic():
            campaign, _ = cls.objects.select_for_update().get_or_create(key='founding-100')
            existing = PermanentVipClaim.objects.filter(user=user).first()
            if not existing:
                if campaign.claimed_count >= cls.LIMIT:
                    raise UserErrors.PERMANENT_VIP_CAMPAIGN_FULL
                if not user.email_verified_at or not user.phone_verified_at or user.effective_growth_level() < 6:
                    raise UserErrors.PERMANENT_VIP_NOT_ELIGIBLE

                slot = campaign.claimed_count + 1
                PermanentVipClaim.objects.create(user=user, slot=slot)
                campaign.claimed_count = slot
                campaign.save(update_fields=['claimed_count'])
                User.objects.filter(id=user.id).update(is_permanent_vip=True)
                user.is_permanent_vip = True
        user.award_growth('vip:permanent')
        return cls.status_for(user)


class PermanentVipClaim(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='permanent_vip_claim')
    slot = models.PositiveSmallIntegerField(unique=True)
    claimed_at = models.DateTimeField(auto_now_add=True)


class UserEmojiUsage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='emoji_usages')
    emoji = models.CharField(max_length=64)
    use_count = models.PositiveIntegerField(default=0)
    last_used_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'emoji'], name='unique_user_emoji_usage'),
        ]

    @classmethod
    def record_text(cls, user, text):
        counts = extract_emoji_counts(text)
        now = timezone.now()
        for emoji, count in counts.items():
            updated = cls.objects.filter(user=user, emoji=emoji).update(
                use_count=F('use_count') + count,
                last_used_at=now,
            )
            if not updated:
                try:
                    cls.objects.create(user=user, emoji=emoji, use_count=count, last_used_at=now)
                except IntegrityError:
                    cls.objects.filter(user=user, emoji=emoji).update(
                        use_count=F('use_count') + count,
                        last_used_at=now,
                    )
        return counts

    @classmethod
    def top_for_user(cls, user, limit=5):
        now = timezone.now()
        rows = list(cls.objects.filter(user=user).order_by('-last_used_at')[:200])
        rows.sort(
            key=lambda row: math.log1p(row.use_count) * math.exp(
                -max(0, (now - row.last_used_at).total_seconds()) / (30 * 86400)
            ),
            reverse=True,
        )
        return [
            dict(emoji=row.emoji, use_count=row.use_count, last_used_at=row.last_used_at.timestamp())
            for row in rows[:limit]
        ]


class RefreshToken(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='refresh_tokens',
    )
    jti = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    def revoke(self):
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=['revoked_at'])


class OfficialLoginTicket(models.Model):
    TOKEN_LENGTH = 48
    EXPIRE_SECONDS = 60

    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='official_login_tickets', db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='official_login_tickets', db_index=True)
    token = models.CharField(max_length=96, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def issue(cls, space):
        official_user = space.official_user or space.ensure_official_user()
        now = timezone.now()
        cls.objects.filter(
            space=space,
            user=official_user,
            used_at__isnull=True,
        ).update(used_at=now)
        return cls.objects.create(
            space=space,
            user=official_user,
            token=get_random_string(cls.TOKEN_LENGTH),
            expires_at=now + datetime.timedelta(seconds=cls.EXPIRE_SECONDS),
        )

    @classmethod
    def exchange(cls, token: str):
        token = (token or '').strip()
        item = cls.objects.filter(
            token=token,
            used_at__isnull=True,
        ).select_related('user', 'space').order_by('-created_at').first()
        if item is None:
            raise UserErrors.OFFICIAL_LOGIN_TICKET_INVALID
        if item.expires_at <= timezone.now():
            item.used_at = timezone.now()
            item.save(update_fields=['used_at'])
            raise UserErrors.OFFICIAL_LOGIN_TICKET_EXPIRED
        item.used_at = timezone.now()
        item.save(update_fields=['used_at'])
        return item.user


class AccountSwitchTicket(models.Model):
    TOKEN_LENGTH = 48
    EXPIRE_SECONDS = 60

    source_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='issued_account_switch_tickets')
    target_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='account_switch_tickets')
    token = models.CharField(max_length=96, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def available_targets(cls, user):
        contact_filter = Q()
        if user.email and user.email_verified_at is not None:
            contact_filter |= Q(email=user.email, email_verified_at__isnull=False)
        if user.phone and user.phone_verified_at is not None:
            contact_filter |= Q(
                phone__in=account_switch_phone_variants(user.phone),
                phone_verified_at__isnull=False,
            )

        targets = User.objects.none()
        if contact_filter:
            targets = User.objects.filter(
                contact_filter,
                is_deleted=False,
                is_private_account=False,
            ).exclude(id=user.id)

        return list(targets.select_related('space').order_by('space__name', 'name'))

    @classmethod
    def issue(cls, source_user, target_user_id):
        target = next((item for item in cls.available_targets(source_user) if item.id == target_user_id), None)
        if target is None:
            raise UserErrors.ACCOUNT_SWITCH_FORBIDDEN
        now = timezone.now()
        cls.objects.filter(source_user=source_user, used_at__isnull=True).update(used_at=now)
        return cls.objects.create(
            source_user=source_user,
            target_user=target,
            token=get_random_string(cls.TOKEN_LENGTH),
            expires_at=now + datetime.timedelta(seconds=cls.EXPIRE_SECONDS),
        )

    @classmethod
    def exchange(cls, token):
        now = timezone.now()
        with transaction.atomic():
            item = cls.objects.select_for_update().select_related('target_user__space').filter(
                token=(token or '').strip(),
                used_at__isnull=True,
            ).first()
            if item is None:
                raise UserErrors.ACCOUNT_SWITCH_TICKET_INVALID
            item.used_at = now
            item.save(update_fields=['used_at'])
            if item.expires_at <= now:
                raise UserErrors.ACCOUNT_SWITCH_TICKET_EXPIRED
            if item.target_user.is_deleted:
                raise UserErrors.ACCOUNT_SWITCH_FORBIDDEN
            return item.target_user


class UserLoginLog(models.Model):
    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='login_logs', db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='login_logs', db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    logged_at = models.DateTimeField(auto_now_add=True, db_index=True)

    @classmethod
    def _normalize_ip(cls, ip):
        raw = (ip or '').strip()
        if not raw:
            return None
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
            return None

    @classmethod
    def create_for_user(cls, user: User, ip: str = None):
        return cls.objects.create(
            space_id=user.space_id,
            user=user,
            ip=cls._normalize_ip(ip),
        )


class UserContactVerificationCode(models.Model):
    CODE_LENGTH = 6
    EXPIRE_SECONDS = 10 * 60

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='contact_verification_codes')
    channel = models.IntegerField(choices=UserNotificationChoice.to_choices(), db_index=True)
    target = models.CharField(max_length=255, db_index=True)
    code = models.CharField(max_length=CODE_LENGTH, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def _normalize_target(cls, channel: int, target: str):
        target = (target or '').strip()
        if channel == UserNotificationChoice.EMAIL:
            return target.lower()
        if channel == UserNotificationChoice.BARK:
            return normalize_bark_endpoint(target)
        return target

    @classmethod
    def issue(cls, user: User, channel: int, target: str):
        normalized_target = cls._normalize_target(channel, target)
        now = timezone.now()
        cls.objects.filter(
            user=user,
            channel=channel,
            target=normalized_target,
            used_at__isnull=True,
        ).update(used_at=now)

        code = get_random_string(cls.CODE_LENGTH, allowed_chars='0123456789')
        return cls.objects.create(
            user=user,
            channel=channel,
            target=normalized_target,
            code=code,
            expires_at=now + datetime.timedelta(seconds=cls.EXPIRE_SECONDS),
        )

    @classmethod
    def verify(cls, user: User, channel: int, target: str, code: str):
        normalized_target = cls._normalize_target(channel, target)
        code = (code or '').strip()
        item = cls.objects.filter(
            user=user,
            channel=channel,
            target=normalized_target,
            code=code,
            used_at__isnull=True,
        ).order_by('-created_at').first()
        if item is None:
            raise UserErrors.CONTACT_CODE_INVALID
        if item.expires_at <= timezone.now():
            raise UserErrors.CONTACT_CODE_EXPIRED
        item.used_at = timezone.now()
        item.save(update_fields=['used_at'])
        return item


class UserPasswordRecoveryChallenge(models.Model):
    CODE_LENGTH = 6
    CODE_EXPIRE_SECONDS = 10 * 60
    RESET_EXPIRE_SECONDS = 10 * 60
    SEND_COOLDOWN_SECONDS = 60
    MAX_ATTEMPTS = 5

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_recovery_challenges')
    channel = models.IntegerField(choices=UserNotificationChoice.to_choices(), db_index=True)
    target = models.CharField(max_length=255)
    code = models.CharField(max_length=CODE_LENGTH)
    attempts = models.PositiveSmallIntegerField(default=0)
    reset_token = models.CharField(max_length=96, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    code_expires_at = models.DateTimeField(db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    reset_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def recovery_channels(user):
        channels = []
        if user.email and user.email_verified_at:
            channels.append(dict(
                channel=UserNotificationChoice.EMAIL,
                type='email',
                masked=UserPasswordRecoveryChallenge.mask_email(user.email),
            ))
        if user.phone and user.phone_verified_at:
            channels.append(dict(
                channel=UserNotificationChoice.SMS,
                type='sms',
                masked=UserPasswordRecoveryChallenge.mask_phone(user.phone),
            ))
        return channels

    @staticmethod
    def mask_email(value):
        local, separator, domain = (value or '').partition('@')
        if not separator:
            return '***'
        visible = local[:2] if len(local) > 2 else local[:1]
        return f'{visible}***@{domain}'

    @staticmethod
    def mask_phone(value):
        raw = (value or '').strip()
        if len(raw) <= 7:
            return f'{raw[:2]}***{raw[-2:]}'
        return f'{raw[:3]}****{raw[-4:]}'

    @classmethod
    def find_user(cls, space, name):
        user = User.objects.filter(
            space=space,
            lower_name=(name or '').strip().lower(),
            role=UserRoleChoice.MEMBER,
            is_deleted=False,
        ).first()
        if user is None or not user.has_password or not cls.recovery_channels(user):
            raise UserErrors.PASSWORD_RECOVERY_UNAVAILABLE
        return user

    @classmethod
    def issue(cls, user, channel):
        if channel == UserNotificationChoice.EMAIL:
            target = user.email if user.email_verified_at else None
        elif channel == UserNotificationChoice.SMS:
            target = user.phone if user.phone_verified_at else None
        else:
            target = None
        if not target:
            raise UserErrors.PASSWORD_RECOVERY_CHANNEL_INVALID

        now = timezone.now()
        latest = cls.objects.filter(user=user).order_by('-created_at').first()
        if latest and latest.created_at > now - datetime.timedelta(seconds=cls.SEND_COOLDOWN_SECONDS):
            raise UserErrors.PASSWORD_RECOVERY_TOO_FREQUENT
        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
        return cls.objects.create(
            user=user,
            channel=channel,
            target=target,
            code=get_random_string(cls.CODE_LENGTH, allowed_chars='0123456789'),
            code_expires_at=now + datetime.timedelta(seconds=cls.CODE_EXPIRE_SECONDS),
        )

    @classmethod
    def verify_code(cls, challenge_id, code):
        now = timezone.now()
        with transaction.atomic():
            challenge = cls.objects.select_for_update().filter(id=challenge_id, used_at__isnull=True).first()
            if challenge is None:
                raise UserErrors.PASSWORD_RECOVERY_CODE_INVALID
            if challenge.code_expires_at <= now:
                challenge.used_at = now
                challenge.save(update_fields=['used_at'])
                raise UserErrors.PASSWORD_RECOVERY_CODE_EXPIRED
            if challenge.attempts >= cls.MAX_ATTEMPTS:
                raise UserErrors.PASSWORD_RECOVERY_ATTEMPTS_EXCEEDED
            if challenge.code != (code or '').strip():
                challenge.attempts += 1
                challenge.save(update_fields=['attempts'])
                if challenge.attempts >= cls.MAX_ATTEMPTS:
                    raise UserErrors.PASSWORD_RECOVERY_ATTEMPTS_EXCEEDED
                raise UserErrors.PASSWORD_RECOVERY_CODE_INVALID
            challenge.verified_at = now
            challenge.reset_token = get_random_string(64)
            challenge.reset_expires_at = now + datetime.timedelta(seconds=cls.RESET_EXPIRE_SECONDS)
            challenge.save(update_fields=['verified_at', 'reset_token', 'reset_expires_at'])
            return challenge

    @classmethod
    def reset_password(cls, reset_token, new_password):
        now = timezone.now()
        with transaction.atomic():
            challenge = cls.objects.select_for_update().select_related('user').filter(
                reset_token=(reset_token or '').strip(),
                used_at__isnull=True,
                verified_at__isnull=False,
            ).first()
            if challenge is None:
                raise UserErrors.PASSWORD_RECOVERY_TOKEN_INVALID
            if not challenge.reset_expires_at or challenge.reset_expires_at <= now:
                challenge.used_at = now
                challenge.save(update_fields=['used_at'])
                raise UserErrors.PASSWORD_RECOVERY_TOKEN_EXPIRED
            challenge.user.set_password(new_password)
            RefreshToken.objects.filter(user=challenge.user, revoked_at__isnull=True).update(revoked_at=now)
            cls.objects.filter(user=challenge.user, used_at__isnull=True).update(used_at=now)
            return challenge.user


class NotificationEventTypeChoice(Choice):
    DIRECT_MESSAGE = 1
    GROUP_MESSAGE = 2
    GROUP_INVITE = 3
    SYSTEM = 4
    SQUARE_STATEMENT_LIKE = 5
    SQUARE_STATEMENT_COMMENT = 6
    SQUARE_COMMENT_LIKE = 7
    SQUARE_COMMENT_REPLY = 8


class NotificationRouteChannelChoice(Choice):
    WEB = 0
    EMAIL = 1
    SMS = 2
    BARK = 3


class NotificationTopicChoice(Choice):
    CHAT = 1
    SQUARE_STATEMENT_LIKE = 2
    SQUARE_STATEMENT_COMMENT = 3
    SQUARE_COMMENT_LIKE = 4
    SQUARE_COMMENT_REPLY = 5
    ONLINE = 6


class NotificationAudienceChoice(Choice):
    ANY = 0
    FRIEND = 1
    OTHER = 2


class NotificationDeliveryStatusChoice(Choice):
    PENDING = 0
    SENT = 1
    FAILED = 2
    SKIPPED = 3


class WebPushSubscription(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='web_push_subscriptions', db_index=True)
    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='web_push_subscriptions', db_index=True)
    endpoint = models.TextField()
    endpoint_digest = models.CharField(max_length=64, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    origin = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=255, blank=True, default='')
    enabled = models.BooleanField(default=True, db_index=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def register(
        cls,
        user: User,
        endpoint: str,
        p256dh: str,
        auth: str,
        origin: str,
        user_agent: str = '',
    ):
        normalized_endpoint = (endpoint or '').strip()
        if not normalized_endpoint or not p256dh or not auth or not origin:
            raise UserErrors.WEB_PUSH_SUBSCRIPTION_INVALID
        endpoint_digest = hashlib.sha256(normalized_endpoint.encode('utf-8')).hexdigest()
        subscription, _created = cls.objects.update_or_create(
            endpoint_digest=endpoint_digest,
            defaults=dict(
                user=user,
                space_id=user.space_id,
                endpoint=normalized_endpoint,
                p256dh=p256dh.strip(),
                auth=auth.strip(),
                origin=origin.strip(),
                user_agent=(user_agent or '')[:255],
                enabled=True,
            ),
        )
        return subscription

    @classmethod
    def active_for_user(cls, user: User):
        return cls.objects.filter(
            user=user,
            space_id=user.space_id,
            enabled=True,
        )

    def json(self):
        return self.dictify(
            'endpoint',
            'origin',
            'enabled',
            'last_seen_at',
        )


class NotificationPreference(models.Model):
    BARK_ICON_NONE = 0
    BARK_ICON_SPACE = 1
    BARK_ICON_ACTOR = 2

    CHANNEL_DEFAULT_THRESHOLDS = {
        UserNotificationChoice.EMAIL: 30,
        UserNotificationChoice.SMS: 15,
        UserNotificationChoice.BARK: 5,
    }

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notification_preferences')
    channel = models.IntegerField(choices=UserNotificationChoice.to_choices())
    enabled = models.BooleanField(default=False)
    offline_threshold_minutes = models.PositiveIntegerField(default=30)
    hide_message_content = models.BooleanField(default=False)
    hidden_direct_message_title = models.CharField(max_length=80, blank=True, default='')
    hidden_direct_message_text = models.CharField(max_length=255, blank=True, default='')
    hidden_group_message_title = models.CharField(max_length=80, blank=True, default='')
    hidden_group_message_text = models.CharField(max_length=255, blank=True, default='')
    friend_online_message_title = models.CharField(max_length=80, blank=True, default='')
    friend_online_message_text = models.CharField(max_length=255, blank=True, default='')
    open_chat_on_tap = models.BooleanField(default=True)
    bark_icon_mode = models.PositiveSmallIntegerField(default=BARK_ICON_SPACE)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'channel')

    @classmethod
    def supported_channels(cls):
        return (
            UserNotificationChoice.EMAIL,
            UserNotificationChoice.SMS,
            UserNotificationChoice.BARK,
        )


    @classmethod
    def _default_enabled(cls, user: User, channel: int):
        if channel == UserNotificationChoice.EMAIL:
            return bool(user.email) and user.email_verified_at is not None
        return False

    @classmethod
    def _default_threshold(cls, channel: int):
        return cls.CHANNEL_DEFAULT_THRESHOLDS.get(channel, 30)

    @classmethod
    def ensure_defaults(cls, user: User):
        prefs = []
        for channel in cls.supported_channels():
            pref, _created = cls.objects.get_or_create(
                user=user,
                channel=channel,
                defaults=dict(
                    enabled=cls._default_enabled(user, channel),
                    offline_threshold_minutes=cls._default_threshold(channel),
                    bark_icon_mode=cls.BARK_ICON_SPACE,
                ),
            )
            prefs.append(pref)
        return sorted(prefs, key=lambda x: x.channel)

    @classmethod
    def set_preference(
        cls,
        user: User,
        channel: int,
        enabled=None,
        offline_threshold_minutes=None,
        hide_message_content=None,
        hidden_direct_message_title=None,
        hidden_direct_message_text=None,
        hidden_group_message_title=None,
        hidden_group_message_text=None,
        friend_online_message_title=None,
        friend_online_message_text=None,
        open_chat_on_tap=None,
        bark_icon_mode=None,
    ):
        pref, _created = cls.objects.get_or_create(
            user=user,
            channel=channel,
            defaults=dict(
                enabled=cls._default_enabled(user, channel),
                offline_threshold_minutes=cls._default_threshold(channel),
                hide_message_content=False,
                hidden_direct_message_title='',
                hidden_direct_message_text='',
                hidden_group_message_title='',
                hidden_group_message_text='',
                friend_online_message_title='',
                friend_online_message_text='',
                open_chat_on_tap=True,
                bark_icon_mode=cls.BARK_ICON_SPACE,
            ),
        )
        updates = []
        if enabled is not None:
            pref.enabled = bool(enabled)
            updates.append('enabled')
        if offline_threshold_minutes is not None:
            pref.offline_threshold_minutes = offline_threshold_minutes
            updates.append('offline_threshold_minutes')
        if hide_message_content is not None:
            pref.hide_message_content = bool(hide_message_content)
            updates.append('hide_message_content')
        if hidden_direct_message_title is not None:
            pref.hidden_direct_message_title = hidden_direct_message_title.strip()
            updates.append('hidden_direct_message_title')
        if hidden_direct_message_text is not None:
            pref.hidden_direct_message_text = hidden_direct_message_text.strip()
            updates.append('hidden_direct_message_text')
        if hidden_group_message_title is not None:
            pref.hidden_group_message_title = hidden_group_message_title.strip()
            updates.append('hidden_group_message_title')
        if hidden_group_message_text is not None:
            pref.hidden_group_message_text = hidden_group_message_text.strip()
            updates.append('hidden_group_message_text')
        if friend_online_message_title is not None:
            pref.friend_online_message_title = friend_online_message_title.strip()
            updates.append('friend_online_message_title')
        if friend_online_message_text is not None:
            pref.friend_online_message_text = friend_online_message_text.strip()
            updates.append('friend_online_message_text')
        if open_chat_on_tap is not None:
            pref.open_chat_on_tap = bool(open_chat_on_tap)
            updates.append('open_chat_on_tap')
        if bark_icon_mode is not None:
            pref.bark_icon_mode = bark_icon_mode
            updates.append('bark_icon_mode')
        if updates:
            pref.save(update_fields=updates)
        return pref

    def json(self):
        return self.dictify(
            'channel',
            'enabled',
            'offline_threshold_minutes',
            'hide_message_content',
            'hidden_direct_message_title',
            'hidden_direct_message_text',
            'hidden_group_message_title',
            'hidden_group_message_text',
            'friend_online_message_title',
            'friend_online_message_text',
            'open_chat_on_tap',
            'bark_icon_mode',
        )


class NotificationTopicPreference(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notification_topic_preferences')
    channel = models.IntegerField(choices=NotificationRouteChannelChoice.to_choices())
    topic = models.IntegerField(choices=NotificationTopicChoice.to_choices())
    audience = models.IntegerField(choices=NotificationAudienceChoice.to_choices(), default=NotificationAudienceChoice.ANY)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['user', 'channel', 'topic', 'audience'],
            name='unique_notification_topic_pref',
        )]

    @classmethod
    def default_enabled(cls, channel, topic):
        return channel != NotificationRouteChannelChoice.SMS and topic != NotificationTopicChoice.ONLINE

    @classmethod
    def is_enabled_for_event(cls, event, channel):
        topic = event.topic()
        if topic is None:
            return True
        audience = event.audience()
        pref = cls.objects.filter(user=event.user, channel=channel, topic=topic, audience=audience).first()
        if pref is None and audience != NotificationAudienceChoice.ANY:
            pref = cls.objects.filter(
                user=event.user, channel=channel, topic=topic, audience=NotificationAudienceChoice.ANY,
            ).first()
        return pref.enabled if pref else cls.default_enabled(channel, topic)

    @classmethod
    def matrix(cls, user):
        existing = {(item.channel, item.topic, item.audience): item.enabled for item in cls.objects.filter(user=user)}
        square_topics = {
            NotificationTopicChoice.SQUARE_STATEMENT_LIKE,
            NotificationTopicChoice.SQUARE_STATEMENT_COMMENT,
            NotificationTopicChoice.SQUARE_COMMENT_LIKE,
            NotificationTopicChoice.SQUARE_COMMENT_REPLY,
        }
        rows = []
        for channel in range(4):
            for topic in range(1, 7):
                audiences = (NotificationAudienceChoice.FRIEND, NotificationAudienceChoice.OTHER) if topic in square_topics else (NotificationAudienceChoice.ANY,)
                for audience in audiences:
                    rows.append(dict(
                        channel=channel,
                        topic=topic,
                        audience=audience,
                        enabled=existing.get((channel, topic, audience), cls.default_enabled(channel, topic)),
                    ))
        return rows

    @classmethod
    def set_enabled(cls, user, channel, topic, audience, enabled):
        pref, _ = cls.objects.update_or_create(
            user=user, channel=channel, topic=topic, audience=audience, defaults=dict(enabled=enabled),
        )
        return pref


class UserWebReminderPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='web_reminder_preference')
    sound_enabled = models.BooleanField(default=True)
    title_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def ensure(cls, user: User):
        pref, _created = cls.objects.get_or_create(
            user=user,
            defaults=dict(
                sound_enabled=True,
                title_enabled=True,
            ),
        )
        return pref

    @classmethod
    def set_preference(cls, user: User, sound_enabled=None, title_enabled=None):
        pref = cls.ensure(user)
        updates = []
        if sound_enabled is not None:
            pref.sound_enabled = bool(sound_enabled)
            updates.append('sound_enabled')
        if title_enabled is not None:
            pref.title_enabled = bool(title_enabled)
            updates.append('title_enabled')
        if updates:
            pref.save(update_fields=updates)
        return pref

    def json(self):
        return self.dictify(
            'sound_enabled',
            'title_enabled',
        )


class UserGestureLockPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='gesture_lock_preference')
    enabled = models.BooleanField(default=False)
    pattern_hash = models.CharField(max_length=128, blank=True, default='')
    salt = models.CharField(max_length=64, blank=True, default='')
    lock_after_minutes = models.PositiveSmallIntegerField(default=User.vldt.GESTURE_LOCK_MIN_MINUTES)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def ensure(cls, user: User):
        pref, _created = cls.objects.get_or_create(user=user)
        return pref

    @classmethod
    def normalize_lock_after_minutes(cls, value):
        try:
            minutes = int(value)
        except (TypeError, ValueError):
            minutes = User.vldt.GESTURE_LOCK_MIN_MINUTES
        return min(
            User.vldt.GESTURE_LOCK_MAX_MINUTES,
            max(User.vldt.GESTURE_LOCK_MIN_MINUTES, minutes),
        )

    @classmethod
    def set_preference(
            cls,
            user: User,
            enabled=None,
            pattern_hash=None,
            salt=None,
            lock_after_minutes=None,
    ):
        pref = cls.ensure(user)
        updates = []
        if enabled is not None:
            pref.enabled = bool(enabled)
            updates.append('enabled')
        if pattern_hash is not None:
            pref.pattern_hash = pattern_hash.strip()
            updates.append('pattern_hash')
        if salt is not None:
            pref.salt = salt.strip()
            updates.append('salt')
        if lock_after_minutes is not None:
            pref.lock_after_minutes = cls.normalize_lock_after_minutes(lock_after_minutes)
            updates.append('lock_after_minutes')
        if updates:
            pref.save(update_fields=updates)
        return pref

    def json(self):
        return self.dictify(
            'enabled',
            'pattern_hash',
            'salt',
            'lock_after_minutes',
        )


class NotificationEvent(models.Model):
    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='notification_events', db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notification_events', db_index=True)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name='notification_actors',
        null=True,
        blank=True,
    )
    event_type = models.IntegerField(choices=NotificationEventTypeChoice.to_choices(), db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_read = models.BooleanField(default=False, db_index=True)

    def _dictify_created_at(self):
        return self.created_at.timestamp()

    def topic(self):
        return {
            NotificationEventTypeChoice.DIRECT_MESSAGE: NotificationTopicChoice.CHAT,
            NotificationEventTypeChoice.GROUP_MESSAGE: NotificationTopicChoice.CHAT,
            NotificationEventTypeChoice.SQUARE_STATEMENT_LIKE: NotificationTopicChoice.SQUARE_STATEMENT_LIKE,
            NotificationEventTypeChoice.SQUARE_STATEMENT_COMMENT: NotificationTopicChoice.SQUARE_STATEMENT_COMMENT,
            NotificationEventTypeChoice.SQUARE_COMMENT_LIKE: NotificationTopicChoice.SQUARE_COMMENT_LIKE,
            NotificationEventTypeChoice.SQUARE_COMMENT_REPLY: NotificationTopicChoice.SQUARE_COMMENT_REPLY,
        }.get(self.event_type, NotificationTopicChoice.ONLINE if (self.payload or {}).get('kind') == 'peer_online' else None)

    def audience(self):
        if not self.actor_id:
            return NotificationAudienceChoice.ANY
        if self.topic() not in {
            NotificationTopicChoice.SQUARE_STATEMENT_LIKE,
            NotificationTopicChoice.SQUARE_STATEMENT_COMMENT,
            NotificationTopicChoice.SQUARE_COMMENT_LIKE,
            NotificationTopicChoice.SQUARE_COMMENT_REPLY,
        }:
            return NotificationAudienceChoice.ANY
        from Friendship.models import Friendship, FriendshipStatusChoice
        is_friend = Friendship.objects.filter(
            space_id=self.space_id,
            status=FriendshipStatusChoice.ACCEPTED,
        ).filter(
            Q(user_low_id=self.user_id, user_high_id=self.actor_id)
            | Q(user_low_id=self.actor_id, user_high_id=self.user_id)
        ).exists()
        return NotificationAudienceChoice.FRIEND if is_friend else NotificationAudienceChoice.OTHER

    def json(self):
        return dict(
            notification_event_id=self.id,
            event_type=self.event_type,
            topic=self.topic(),
            audience=self.audience(),
            actor=self.actor.tiny_json() if self.actor_id else None,
            payload=self.payload or {},
            is_read=self.is_read,
            created_at=self.created_at.timestamp(),
        )

    def render_delivery_message(
        self,
        hide_message_content=False,
        hidden_direct_message_title='',
        hidden_direct_message_text='',
        hidden_group_message_title='',
        hidden_group_message_text='',
        friend_online_message_title='',
        friend_online_message_text='',
    ):
        language = self.user.language if self.user_id else translation.get_language()
        with translation.override(language):
            return self._render_delivery_message(
                hide_message_content=hide_message_content,
                hidden_direct_message_title=hidden_direct_message_title,
                hidden_direct_message_text=hidden_direct_message_text,
                hidden_group_message_title=hidden_group_message_title,
                hidden_group_message_text=hidden_group_message_text,
                friend_online_message_title=friend_online_message_title,
                friend_online_message_text=friend_online_message_text,
            )

    def _render_delivery_message(
        self,
        hide_message_content=False,
        hidden_direct_message_title='',
        hidden_direct_message_text='',
        hidden_group_message_title='',
        hidden_group_message_text='',
        friend_online_message_title='',
        friend_online_message_text='',
    ):
        payload = self.payload or {}
        actor_name = self.actor.name if self.actor_id else None

        if self.event_type == NotificationEventTypeChoice.DIRECT_MESSAGE:
            if hide_message_content:
                return str(hidden_direct_message_title.strip() or _('New direct message')), str(
                    hidden_direct_message_text.strip() or _('You received a new direct message.')
                )
            title = _('New direct message')
            body = payload.get('content') or _('You have received a new direct message.')
            if actor_name:
                title = _('New message from {name}').format(name=actor_name)
            return str(title), str(body)

        if self.event_type == NotificationEventTypeChoice.GROUP_MESSAGE:
            if hide_message_content:
                return str(hidden_group_message_title.strip() or _('New group message')), str(
                    hidden_group_message_text.strip() or _('You received a new group message.')
                )
            title = _('New group message')
            body = payload.get('content') or _('You have received a new group message.')
            if actor_name:
                title = _('New group message from {name}').format(name=actor_name)
            return str(title), str(body)

        kind = payload.get('kind')
        if kind == 'friend_request':
            title = _('New friend request')
            body = _('You have received a new friend request.')
            if actor_name:
                body = _('{name} sent you a friend request.').format(name=actor_name)
            return str(title), str(body)
        if kind == 'friend_request_accepted':
            title = _('Friend request accepted')
            body = _('Your friend request has been accepted.')
            if actor_name:
                body = _('{name} accepted your friend request.').format(name=actor_name)
            return str(title), str(body)
        if kind == 'group_invite':
            title = _('Group invite')
            group_name = payload.get('chat_name') or _('a group')
            body = _('You are invited to join {group}.').format(group=group_name)
            return str(title), str(body)
        if kind == 'group_invite_response':
            title = _('Group invite response')
            accepted = payload.get('accepted')
            body = _('A user responded to your invite.')
            if accepted is True:
                body = _('A user accepted your group invite.')
            elif accepted is False:
                body = _('A user rejected your group invite.')
            return str(title), str(body)
        if kind == 'peer_online':
            title = friend_online_message_title.strip() or _('Friend online')
            body = friend_online_message_text.strip() or _('{name} is online now.').format(name=actor_name or _('Your friend'))
            return str(title), str(body)

        square_messages = {
            NotificationEventTypeChoice.SQUARE_STATEMENT_LIKE: (_('New like'), _('{name} liked your statement.')),
            NotificationEventTypeChoice.SQUARE_STATEMENT_COMMENT: (_('New comment'), _('{name} commented on your statement.')),
            NotificationEventTypeChoice.SQUARE_COMMENT_LIKE: (_('Comment liked'), _('{name} liked your comment.')),
            NotificationEventTypeChoice.SQUARE_COMMENT_REPLY: (_('New reply'), _('{name} replied to your comment.')),
        }
        if self.event_type in square_messages:
            title, template = square_messages[self.event_type]
            return str(title), str(template.format(name=actor_name or _('Someone')))

        return str(_('System notification')), str(_('You have a new notification.'))

    @classmethod
    def _message_recipients(cls, chat, actor: User):
        from Chat.models import ChatMember, ChatMemberStatusChoice

        users = [
            item.user for item in ChatMember.objects.filter(
                chat=chat,
                status=ChatMemberStatusChoice.ACTIVE,
            ).select_related('user')
        ]
        return [user for user in users if user.id != actor.id and not user.is_deleted]

    @classmethod
    def _message_event_type(cls, chat):
        if chat.group:
            return NotificationEventTypeChoice.GROUP_MESSAGE
        return NotificationEventTypeChoice.DIRECT_MESSAGE

    @classmethod
    def emit_message_notifications(cls, message, actor: User, enqueue=True):
        event_type = cls._message_event_type(message.chat)
        payload = dict(
            chat_id=message.chat_id,
            message_id=message.id,
            message_type=message.type,
            content=message.preview_text(),
        )
        created_events = []
        for user in cls._message_recipients(message.chat, actor):
            event = cls.objects.create(
                space_id=user.space_id,
                user=user,
                actor=actor,
                event_type=event_type,
                payload=payload,
            )
            created_events.append(event)
        if enqueue:
            cls._enqueue_deliveries_after_commit([event.id for event in created_events])
        return created_events

    @classmethod
    def _enqueue_deliveries_after_commit(cls, event_ids):
        event_ids = tuple(event_ids)
        if not event_ids:
            return

        def start():
            threading.Thread(
                target=cls._enqueue_deliveries,
                args=(event_ids,),
                daemon=True,
                name='notification-delivery',
            ).start()

        transaction.on_commit(start)

    @classmethod
    def _enqueue_deliveries(cls, event_ids):
        close_old_connections()
        try:
            events = cls.objects.filter(id__in=event_ids).select_related('user', 'actor', 'space')
            events_by_id = {event.id: event for event in events}
            for event_id in event_ids:
                event = events_by_id.get(event_id)
                if event is not None:
                    NotificationDelivery.enqueue_for_event(event)
        except Exception:
            logger.exception('Failed to enqueue notification deliveries for events %s', event_ids)
        finally:
            close_old_connections()

    @classmethod
    def emit_system_event(cls, user: User, actor: User, payload: dict):
        event = cls.objects.create(
            space_id=user.space_id,
            user=user,
            actor=actor,
            event_type=NotificationEventTypeChoice.SYSTEM,
            payload=payload or {},
        )
        cls._enqueue_deliveries_after_commit([event.id])
        return event

    @classmethod
    def emit_square_event(cls, user, actor, event_type, statement_id, comment_id=None):
        if user.id == actor.id:
            return None
        event = cls.objects.create(
            space_id=user.space_id,
            user=user,
            actor=actor,
            event_type=event_type,
            payload=dict(statement_id=statement_id, comment_id=comment_id),
        )
        cls._enqueue_deliveries_after_commit([event.id])
        return event


class NotificationDelivery(models.Model):
    EMAIL_BATCH_MESSAGE_LIMIT = 8
    EMAIL_BATCH_BODY_LIMIT = 1200
    MESSAGE_EVENT_TYPES = (
        NotificationEventTypeChoice.DIRECT_MESSAGE,
        NotificationEventTypeChoice.GROUP_MESSAGE,
    )

    event = models.ForeignKey(NotificationEvent, on_delete=models.CASCADE, related_name='deliveries', db_index=True)
    channel = models.IntegerField(choices=UserNotificationChoice.to_choices())
    status = models.IntegerField(
        choices=NotificationDeliveryStatusChoice.to_choices(),
        default=NotificationDeliveryStatusChoice.PENDING,
        db_index=True,
    )
    detail = models.CharField(max_length=255, null=True, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def _channel_available(cls, user: User, channel: int):
        if channel == UserNotificationChoice.EMAIL:
            return bool(user.email) and user.email_verified_at is not None
        if channel == UserNotificationChoice.SMS:
            return bool(user.phone) and user.phone_verified_at is not None
        if channel == UserNotificationChoice.BARK:
            return bool(user.bark) and user.bark_verified_at is not None
        return False

    @classmethod
    def _channel_target(cls, user: User, channel: int):
        if channel == UserNotificationChoice.EMAIL:
            return user.email
        if channel == UserNotificationChoice.SMS:
            return user.phone
        if channel == UserNotificationChoice.BARK:
            return user.bark
        return None

    @classmethod
    def _offline_threshold_reached(cls, user: User, threshold_minutes: int):
        threshold_seconds = max(1, int(threshold_minutes)) * 60
        offline_seconds = (timezone.now() - user.last_heartbeat).total_seconds()
        return offline_seconds >= threshold_seconds

    def _bark_chat_url(self, pref: NotificationPreference):
        if not pref.open_chat_on_tap:
            return None
        if self.event.event_type not in (
            NotificationEventTypeChoice.DIRECT_MESSAGE,
            NotificationEventTypeChoice.GROUP_MESSAGE,
        ):
            return None
        chat_id = (self.event.payload or {}).get('chat_id')
        if not chat_id:
            return None
        space_slug = getattr(self.event.space, 'slug', None)
        if not space_slug:
            return None
        return f'{FRONTEND_BASE_URL}/{space_slug}/app/chats/{chat_id}'

    def _bark_icon_url(self, pref: NotificationPreference):
        if pref.bark_icon_mode == NotificationPreference.BARK_ICON_SPACE:
            official_user = self.event.space.official_user
            return official_user.tiny_json().get('avatar_uri') if official_user else None
        if pref.bark_icon_mode == NotificationPreference.BARK_ICON_ACTOR:
            actor = self.event.actor
            return actor.tiny_json().get('avatar_uri') if actor else None
        return None

    @classmethod
    def _is_message_email_delivery(cls, delivery):
        return (
            delivery.channel == UserNotificationChoice.EMAIL
            and delivery.event.event_type in cls.MESSAGE_EVENT_TYPES
        )

    @classmethod
    def _truncate_email_line(cls, value, limit=86):
        normalized = ' '.join(str(value or '').split())
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit - 1].rstrip() + '…'

    @classmethod
    def _render_email_batch_title(cls, deliveries):
        names = []
        seen = set()
        for delivery in deliveries:
            actor = delivery.event.actor
            actor_id = getattr(delivery.event, 'actor_id', None)
            name = actor.name if actor_id else ''
            if not actor_id or not name or actor_id in seen:
                continue
            seen.add(actor_id)
            names.append(name)

        if not names:
            return str(_('New messages'))
        if len(names) == 1:
            return str(_('Messages from {name}').format(name=names[0]))
        if len(names) == 2:
            return str(_('Messages from {first} and {second}').format(first=names[0], second=names[1]))
        return str(_('Messages from {name} and {count} people').format(name=names[0], count=len(names)))

    @classmethod
    def _render_email_batch_body(cls, deliveries, pref: NotificationPreference):
        grouped = []
        indexes = {}
        hide_message_content = bool(pref.hide_message_content)
        for delivery in deliveries:
            actor = delivery.event.actor
            actor_key = getattr(delivery.event, 'actor_id', None) or f'event-{delivery.event_id}'
            actor_name = actor.name if actor else str(_('Someone'))
            if actor_key not in indexes:
                indexes[actor_key] = len(grouped)
                grouped.append(dict(name=actor_name, items=[]))
            _title, body = delivery.event.render_delivery_message(
                hide_message_content=hide_message_content,
                hidden_direct_message_title=pref.hidden_direct_message_title,
                hidden_direct_message_text=pref.hidden_direct_message_text,
                hidden_group_message_title=pref.hidden_group_message_title,
                hidden_group_message_text=pref.hidden_group_message_text,
                friend_online_message_title=pref.friend_online_message_title,
                friend_online_message_text=pref.friend_online_message_text,
            )
            grouped[indexes[actor_key]]['items'].append(body)

        lines = [str(_('You have unread messages:')), '']
        remaining = cls.EMAIL_BATCH_MESSAGE_LIMIT
        omitted = 0
        for group in grouped:
            if remaining <= 0:
                omitted += len(group['items'])
                continue
            items = group['items']
            lines.append(str(_('{name} ({count} messages)').format(name=group['name'], count=len(items))))
            for item in items[:remaining]:
                lines.append(f'- {cls._truncate_email_line(item)}')
            if len(items) > remaining:
                omitted += len(items) - remaining
            remaining -= min(len(items), remaining)
            lines.append('')

        if omitted > 0:
            lines.append(str(_('And {count} more messages.').format(count=omitted)))
        lines.append(str(_('Open Sermo Yanlang to reply.')))
        body = '\n'.join(lines).strip()
        if len(body) <= cls.EMAIL_BATCH_BODY_LIMIT:
            return body
        return body[:cls.EMAIL_BATCH_BODY_LIMIT - 1].rstrip() + '…'

    @classmethod
    def _attempt_send_email_batch(cls, user: User, pref: NotificationPreference, limit=50):
        target = cls._channel_target(user, UserNotificationChoice.EMAIL)
        if not target:
            return []

        deliveries = list(
            cls.objects.filter(
                status=NotificationDeliveryStatusChoice.PENDING,
                channel=UserNotificationChoice.EMAIL,
                event__user=user,
                event__event_type__in=cls.MESSAGE_EVENT_TYPES,
            )
            .select_related('event', 'event__user', 'event__actor', 'event__space')
            .order_by('event__created_at', 'id')[:limit]
        )
        if not deliveries:
            return []

        with translation.override(user.language):
            title = cls._render_email_batch_title(deliveries)
            body = cls._render_email_batch_body(deliveries, pref)
        attempted_at = timezone.now()
        try:
            notificator.mail(
                target,
                title=title,
                body=body,
                recipient_name=user.name,
            )
            ok, detail = True, None
        except NotificatorAPIError as err:
            ok, detail = False, str(err)
        except Exception as err:
            ok, detail = False, str(err)

        status = NotificationDeliveryStatusChoice.SENT if ok else NotificationDeliveryStatusChoice.FAILED
        detail = None if ok else str(detail)[:255]
        ids = [delivery.id for delivery in deliveries]
        cls.objects.filter(id__in=ids).update(status=status, detail=detail, attempted_at=attempted_at)
        for delivery in deliveries:
            delivery.status = status
            delivery.detail = detail
            delivery.attempted_at = attempted_at
        return deliveries

    def _attempt_send(self, pref: NotificationPreference):
        target = self._channel_target(self.event.user, self.channel)
        if not target:
            self.status = NotificationDeliveryStatusChoice.SKIPPED
            self.detail = 'channel_unavailable'
            self.attempted_at = timezone.now()
            self.save(update_fields=['status', 'detail', 'attempted_at'])
            return self

        hide_message_content = bool(pref.hide_message_content) and self.channel in (
            UserNotificationChoice.EMAIL,
            UserNotificationChoice.BARK,
        )
        title, body = self.event.render_delivery_message(
            hide_message_content=hide_message_content,
            hidden_direct_message_title=pref.hidden_direct_message_title,
            hidden_direct_message_text=pref.hidden_direct_message_text,
            hidden_group_message_title=pref.hidden_group_message_title,
            hidden_group_message_text=pref.hidden_group_message_text,
            friend_online_message_title=pref.friend_online_message_title,
            friend_online_message_text=pref.friend_online_message_text,
        )
        try:
            if self.channel == UserNotificationChoice.EMAIL:
                notificator.mail(
                    target,
                    title=title,
                    body=body,
                    recipient_name=self.event.user.name,
                )
            elif self.channel == UserNotificationChoice.SMS:
                notificator.sms(
                    target,
                    title=title,
                    body=body,
                )
            elif self.channel == UserNotificationChoice.BARK:
                notificator.bark(
                    target,
                    title=title,
                    body=body,
                    icon=self._bark_icon_url(pref),
                    url=self._bark_chat_url(pref),
                )
            else:
                self.status = NotificationDeliveryStatusChoice.FAILED
                self.detail = 'unsupported_channel'
                self.attempted_at = timezone.now()
                self.save(update_fields=['status', 'detail', 'attempted_at'])
                return self
            ok, detail = True, None
        except NotificatorAPIError as err:
            ok, detail = False, str(err)
        except Exception as err:
            ok, detail = False, str(err)

        self.status = NotificationDeliveryStatusChoice.SENT if ok else NotificationDeliveryStatusChoice.FAILED
        self.detail = None if ok else str(detail)[:255]
        self.attempted_at = timezone.now()
        self.save(update_fields=['status', 'detail', 'attempted_at'])
        return self

    @classmethod
    def enqueue_for_event(cls, event: NotificationEvent):
        deliveries = []
        prefs = NotificationPreference.ensure_defaults(event.user)
        for pref in prefs:
            status = NotificationDeliveryStatusChoice.PENDING
            detail = None
            attempted_at = None

            if not NotificationTopicPreference.is_enabled_for_event(event, pref.channel):
                status = NotificationDeliveryStatusChoice.SKIPPED
                detail = 'topic_disabled'
                attempted_at = timezone.now()
            elif not pref.enabled:
                status = NotificationDeliveryStatusChoice.SKIPPED
                detail = 'channel_disabled'
                attempted_at = timezone.now()
            elif not cls._channel_available(event.user, pref.channel):
                status = NotificationDeliveryStatusChoice.SKIPPED
                detail = 'channel_unavailable'
                attempted_at = timezone.now()
            elif not cls._offline_threshold_reached(event.user, pref.offline_threshold_minutes):
                status = NotificationDeliveryStatusChoice.PENDING
                detail = 'waiting_offline_threshold'

            delivery = cls.objects.create(
                event=event,
                channel=pref.channel,
                status=status,
                detail=detail,
                attempted_at=attempted_at,
            )
            if status == NotificationDeliveryStatusChoice.PENDING and detail is None:
                if cls._is_message_email_delivery(delivery):
                    cls._attempt_send_email_batch(event.user, pref)
                else:
                    delivery._attempt_send(pref)
            deliveries.append(delivery)
        deliveries.extend(WebPushDelivery.enqueue_for_event(event))
        return deliveries

    @classmethod
    def process_pending(cls, user: User = None, limit: int = 200):
        query = cls.objects.filter(status=NotificationDeliveryStatusChoice.PENDING).select_related('event', 'event__user')
        if user is not None:
            query = query.filter(event__user=user)
        deliveries = list(query.order_by('created_at')[:limit])
        processed_delivery_ids = set()
        for delivery in deliveries:
            if delivery.id in processed_delivery_ids:
                continue
            pref = NotificationPreference.objects.filter(
                user=delivery.event.user,
                channel=delivery.channel,
            ).first()
            if pref is None:
                delivery.status = NotificationDeliveryStatusChoice.SKIPPED
                delivery.detail = 'preference_missing'
                delivery.attempted_at = timezone.now()
                delivery.save(update_fields=['status', 'detail', 'attempted_at'])
                continue
            if not NotificationTopicPreference.is_enabled_for_event(delivery.event, delivery.channel):
                delivery.status = NotificationDeliveryStatusChoice.SKIPPED
                delivery.detail = 'topic_disabled'
                delivery.attempted_at = timezone.now()
                delivery.save(update_fields=['status', 'detail', 'attempted_at'])
                continue
            if not pref.enabled:
                delivery.status = NotificationDeliveryStatusChoice.SKIPPED
                delivery.detail = 'channel_disabled'
                delivery.attempted_at = timezone.now()
                delivery.save(update_fields=['status', 'detail', 'attempted_at'])
                continue
            if not cls._channel_available(delivery.event.user, delivery.channel):
                delivery.status = NotificationDeliveryStatusChoice.SKIPPED
                delivery.detail = 'channel_unavailable'
                delivery.attempted_at = timezone.now()
                delivery.save(update_fields=['status', 'detail', 'attempted_at'])
                continue
            if not cls._offline_threshold_reached(delivery.event.user, pref.offline_threshold_minutes):
                delivery.detail = 'waiting_offline_threshold'
                delivery.save(update_fields=['detail'])
                continue
            if cls._is_message_email_delivery(delivery):
                batch = cls._attempt_send_email_batch(delivery.event.user, pref)
                processed_delivery_ids.update(item.id for item in batch)
                continue
            delivery.detail = None
            delivery.save(update_fields=['detail'])
            delivery._attempt_send(pref)
        return deliveries


class WebPushDelivery(models.Model):
    event = models.ForeignKey(NotificationEvent, on_delete=models.CASCADE, related_name='web_push_deliveries', db_index=True)
    subscription = models.ForeignKey(WebPushSubscription, on_delete=models.CASCADE, related_name='deliveries', db_index=True)
    status = models.IntegerField(
        choices=NotificationDeliveryStatusChoice.to_choices(),
        default=NotificationDeliveryStatusChoice.PENDING,
        db_index=True,
    )
    detail = models.CharField(max_length=255, null=True, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def enqueue_for_event(cls, event: NotificationEvent):
        deliveries = []
        if not NotificationTopicPreference.is_enabled_for_event(event, NotificationRouteChannelChoice.WEB):
            return deliveries
        subscriptions = list(WebPushSubscription.active_for_user(event.user))
        for subscription in subscriptions:
            delivery = cls.objects.create(event=event, subscription=subscription)
            delivery._attempt_send()
            deliveries.append(delivery)
        return deliveries

    def _payload(self):
        payload = dict(self.event.payload or {})
        official_user = self.event.space.official_user
        payload.update(
            notification_event_id=self.event_id,
            event_type=self.event.event_type,
            space_slug=self.event.space.slug,
            icon=official_user.tiny_json().get('avatar_uri') if official_user else '',
        )
        return payload

    def _attempt_send(self):
        from utils.webpush import WebPushNotConfigured, is_expired_subscription_error, send_web_push

        title, body = self.event.render_delivery_message()
        try:
            send_web_push(
                subscription=self.subscription,
                title=title,
                body=body,
                payload=self._payload(),
            )
            self.status = NotificationDeliveryStatusChoice.SENT
            self.detail = None
        except WebPushNotConfigured as err:
            self.status = NotificationDeliveryStatusChoice.SKIPPED
            self.detail = str(err)[:255]
        except Exception as err:
            self.status = NotificationDeliveryStatusChoice.FAILED
            self.detail = str(err)[:255]
            if is_expired_subscription_error(err):
                self.subscription.enabled = False
                self.subscription.save(update_fields=['enabled'])

        self.attempted_at = timezone.now()
        self.save(update_fields=['status', 'detail', 'attempted_at'])
        return self
