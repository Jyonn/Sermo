import datetime
import ipaddress
import re

from notificator import NotificatorAPIError
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.translation import gettext as _
from pypinyin import lazy_pinyin
from smartdjango import models, Choice

from utils.global_settings import notificator
from utils.qiniu import sign_private_download_url, delete_avatar_by_uri
from User.validators import UserValidator, UserErrors
from utils import function


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


class User(models.Model):
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
    def jwt_login(cls, user_id):
        return cls.index(user_id)

    @classmethod
    def _assert_name_available(cls, space, name):
        lower_name = name.lower()
        if cls.objects.filter(space=space, lower_name=lower_name).exists():
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
        normalized_language = cls.vldt.language(language)
        user = cls.objects.filter(space=space, lower_name=name.lower()).first()
        if user is None:
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
        self.last_heartbeat = timezone.now()
        self.save(update_fields=['last_heartbeat'])

    def log_login(self, ip: str = None):
        return UserLoginLog.create_for_user(self, ip=ip)

    @property
    def is_alive(self):
        current_time = timezone.now()
        return (current_time - self.last_heartbeat).seconds < self.vldt.OFFLINE_MIN_INTERVAL * 60

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
            return sign_private_download_url(avatar_uri)
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
    def set_preference(cls, user: User, channel: int, enabled=None, offline_threshold_minutes=None):
        pref, _created = cls.objects.get_or_create(
            user=user,
            channel=channel,
            defaults=dict(
                enabled=cls._default_enabled(user, channel),
                offline_threshold_minutes=cls._default_threshold(channel),
            ),
        )
        updates = []
        if enabled is not None:
            pref.enabled = bool(enabled)
            updates.append('enabled')
        if offline_threshold_minutes is not None:
            pref.offline_threshold_minutes = offline_threshold_minutes
            updates.append('offline_threshold_minutes')
        if updates:
            pref.save(update_fields=updates)
        return pref

    def json(self):
        return self.dictify('channel', 'enabled', 'offline_threshold_minutes')


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

    def render_delivery_message(self):
        payload = self.payload or {}
        actor_name = self.actor.name if self.actor_id else None

        if self.event_type == NotificationEventTypeChoice.DIRECT_MESSAGE:
            title = _('New direct message')
            body = payload.get('content') or _('You have received a new direct message.')
            if actor_name:
                title = _('New message from {name}').format(name=actor_name)
            return str(title), str(body)

        if self.event_type == NotificationEventTypeChoice.GROUP_MESSAGE:
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

    def _attempt_send(self, pref: NotificationPreference):
        target = self._channel_target(self.event.user, self.channel)
        if not target:
            self.status = NotificationDeliveryStatusChoice.SKIPPED
            self.detail = 'channel_unavailable'
            self.attempted_at = timezone.now()
            self.save(update_fields=['status', 'detail', 'attempted_at'])
            return self

        title, body = self.event.render_delivery_message()
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
                delivery._attempt_send(pref)
            deliveries.append(delivery)
        return deliveries

    @classmethod
    def process_pending(cls, user: User = None, limit: int = 200):
        query = cls.objects.filter(status=NotificationDeliveryStatusChoice.PENDING).select_related('event', 'event__user')
        if user is not None:
            query = query.filter(event__user=user)
        deliveries = list(query.order_by('created_at')[:limit])
        for delivery in deliveries:
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
            delivery.detail = None
            delivery.save(update_fields=['detail'])
            delivery._attempt_send(pref)
        return deliveries
