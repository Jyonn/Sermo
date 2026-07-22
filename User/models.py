import datetime
import hashlib
import ipaddress
import re

from notificator import NotificatorAPIError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.translation import gettext as _
from pypinyin import lazy_pinyin
from smartdjango import models, Choice

from utils.global_settings import notificator
from utils.qiniu import sign_private_download_url, delete_avatar_by_uri, build_avatar_display_uri
from User.validators import UserValidator, UserErrors
from utils import function


FRONTEND_SPACE_HOST_SUFFIX = 'sermo.jyonn.space'


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
    welcome_message = models.CharField(
        max_length=vldt.WELCOME_MESSAGE_MAX_LENGTH,
        default='',
        validators=[vldt.welcome_message],
    )
    avatar_type = models.CharField(
        max_length=16,
        choices=UserAvatarTypeChoice.to_choices(),
        default=UserAvatarTypeChoice.PRESET,
    )
    avatar_uri = models.CharField(
        max_length=255,
        default='',
    )

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
        return self

    def set_language(self, language, save=True):
        normalized = self.vldt.language(language)
        if self.language == normalized:
            return self
        self.language = normalized
        if save:
            self.save(update_fields=['language'])
        return self

    def set_name(self, name, save=True):
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
            self.save(update_fields=['name', 'lower_name', 'name_pinyin'])
        return self

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
        return self

    def set_welcome_message(self, welcome_message, save=True):
        normalized = self.vldt.welcome_message(welcome_message)
        self.welcome_message = normalized
        if save:
            self.save(update_fields=['welcome_message'])
        return self

    def bind_contact(self, channel: int, target: str):
        target = (target or '').strip()
        now = timezone.now()

        if channel == UserNotificationChoice.EMAIL:
            self.email = self._normalize_email(target)
            self.email_verified_at = now
            self.account_level = UserAccountLevelChoice.VERIFIED
            self.save(update_fields=['email', 'email_verified_at', 'account_level'])
            return self
        if channel == UserNotificationChoice.SMS:
            self.phone = target
            self.phone_verified_at = now
            self.save(update_fields=['phone', 'phone_verified_at'])
            return self
        if channel == UserNotificationChoice.BARK:
            self.bark = target
            self.bark_verified_at = now
            self.save(update_fields=['bark', 'bark_verified_at'])
            return self
        raise UserErrors.CONTACT_CHANNEL_INVALID

    def set_preset_avatar(self, preset_id: int, save=True):
        previous_avatar_type = self.avatar_type
        previous_avatar_uri = self.avatar_uri
        self.avatar_type = UserAvatarTypeChoice.PRESET
        self.avatar_uri = self.build_preset_avatar_uri(preset_id)
        if save:
            self.save(update_fields=['avatar_type', 'avatar_uri'])
            self._delete_previous_custom_avatar(previous_avatar_type, previous_avatar_uri, self.avatar_uri)
        return self

    def set_custom_avatar(self, avatar_uri: str, save=True):
        previous_avatar_type = self.avatar_type
        previous_avatar_uri = self.avatar_uri
        self.avatar_type = UserAvatarTypeChoice.CUSTOM
        self.avatar_uri = (avatar_uri or '').strip()
        if save:
            self.save(update_fields=['avatar_type', 'avatar_uri'])
            self._delete_previous_custom_avatar(previous_avatar_type, previous_avatar_uri, self.avatar_uri)
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

    def tiny_json(self):
        return self.dictify('name', 'user_id', 'official', 'avatar_type', 'avatar_uri')

    def jsonl(self):
        return self.dictify(
            'name',
            'user_id',
            'official',
            'verified',
            'is_alive',
            'avatar_type',
            'avatar_uri',
        )

    def json_friend(self):
        return self.dictify(
            'name',
            'name_pinyin',
            'user_id',
            'official',
            'verified',
            'is_alive',
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
        return self.dictify(
            'name',
            'user_id',
            'official',
            'has_password',
            'language',
            'welcome_message',
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
        )


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


class NotificationEventTypeChoice(Choice):
    DIRECT_MESSAGE = 1
    GROUP_MESSAGE = 2
    GROUP_INVITE = 3
    SYSTEM = 4


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
    hidden_direct_message_text = models.CharField(max_length=255, blank=True, default='')
    hidden_group_message_text = models.CharField(max_length=255, blank=True, default='')
    friend_online_message_text = models.CharField(max_length=255, blank=True, default='')
    open_chat_on_tap = models.BooleanField(default=True)
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
        hidden_direct_message_text=None,
        hidden_group_message_text=None,
        friend_online_message_text=None,
        open_chat_on_tap=None,
    ):
        pref, _created = cls.objects.get_or_create(
            user=user,
            channel=channel,
            defaults=dict(
                enabled=cls._default_enabled(user, channel),
                offline_threshold_minutes=cls._default_threshold(channel),
                hide_message_content=False,
                hidden_direct_message_text='',
                hidden_group_message_text='',
                friend_online_message_text='',
                open_chat_on_tap=True,
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
        if hidden_direct_message_text is not None:
            pref.hidden_direct_message_text = hidden_direct_message_text.strip()
            updates.append('hidden_direct_message_text')
        if hidden_group_message_text is not None:
            pref.hidden_group_message_text = hidden_group_message_text.strip()
            updates.append('hidden_group_message_text')
        if friend_online_message_text is not None:
            pref.friend_online_message_text = friend_online_message_text.strip()
            updates.append('friend_online_message_text')
        if open_chat_on_tap is not None:
            pref.open_chat_on_tap = bool(open_chat_on_tap)
            updates.append('open_chat_on_tap')
        if updates:
            pref.save(update_fields=updates)
        return pref

    def json(self):
        return self.dictify(
            'channel',
            'enabled',
            'offline_threshold_minutes',
            'hide_message_content',
            'hidden_direct_message_text',
            'hidden_group_message_text',
            'friend_online_message_text',
            'open_chat_on_tap',
        )


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
    decoy_enabled = models.BooleanField(default=False)
    decoy_pattern_hash = models.CharField(max_length=128, blank=True, default='')
    decoy_salt = models.CharField(max_length=64, blank=True, default='')
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
            decoy_enabled=None,
            decoy_pattern_hash=None,
            decoy_salt=None,
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
        if decoy_enabled is not None:
            pref.decoy_enabled = bool(decoy_enabled)
            updates.append('decoy_enabled')
        if decoy_pattern_hash is not None:
            pref.decoy_pattern_hash = decoy_pattern_hash.strip()
            updates.append('decoy_pattern_hash')
        if decoy_salt is not None:
            pref.decoy_salt = decoy_salt.strip()
            updates.append('decoy_salt')
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
            'decoy_enabled',
            'decoy_pattern_hash',
            'decoy_salt',
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

    def render_delivery_message(
        self,
        hide_message_content=False,
        hidden_direct_message_text='',
        hidden_group_message_text='',
        friend_online_message_text='',
    ):
        payload = self.payload or {}
        actor_name = self.actor.name if self.actor_id else None

        if self.event_type == NotificationEventTypeChoice.DIRECT_MESSAGE:
            if hide_message_content:
                return str(_('New direct message')), str(
                    hidden_direct_message_text.strip() or _('You received a new direct message.')
                )
            title = _('New direct message')
            body = payload.get('content') or _('You have received a new direct message.')
            if actor_name:
                title = _('New message from {name}').format(name=actor_name)
            return str(title), str(body)

        if self.event_type == NotificationEventTypeChoice.GROUP_MESSAGE:
            if hide_message_content:
                return str(_('New group message')), str(
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
            title = _('Friend online')
            body = friend_online_message_text.strip() or _('{name} is online now.').format(name=actor_name or _('Your friend'))
            return str(title), str(body)

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
    def emit_message_notifications(cls, message, actor: User):
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
            NotificationDelivery.enqueue_for_event(event)
            created_events.append(event)
        return created_events

    @classmethod
    def emit_system_event(cls, user: User, actor: User, payload: dict):
        event = cls.objects.create(
            space_id=user.space_id,
            user=user,
            actor=actor,
            event_type=NotificationEventTypeChoice.SYSTEM,
            payload=payload or {},
        )
        NotificationDelivery.enqueue_for_event(event)
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
        return f'https://{space_slug}.{FRONTEND_SPACE_HOST_SUFFIX}/app/chats/{chat_id}'

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
                hidden_direct_message_text=pref.hidden_direct_message_text,
                hidden_group_message_text=pref.hidden_group_message_text,
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
            hidden_direct_message_text=pref.hidden_direct_message_text,
            hidden_group_message_text=pref.hidden_group_message_text,
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

            if not pref.enabled:
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
