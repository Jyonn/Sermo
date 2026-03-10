import datetime

from diq import Dictify
from django.db import models
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.translation import gettext as _
from smartdjango import Choice, Error

from User.validators import BaseUserValidator, UserErrors, ConfigValidator, ConfigErrors
from User import validators
from utils import function


class UserNotificationChoice(Choice):
    UNSET = 0
    EMAIL = 1
    SMS = 2
    BARK = 3


class UserRoleChoice(Choice):
    HOST = 0
    GUEST = 1


class UserAccountLevelChoice(Choice):
    BASIC = 0
    VERIFIED = 1


class BaseUser(models.Model, Dictify):
    vldt = BaseUserValidator

    role = models.IntegerField(choices=UserRoleChoice.to_choices())
    account_level = models.IntegerField(
        choices=UserAccountLevelChoice.to_choices(),
        default=UserAccountLevelChoice.BASIC,
    )

    offline_notification_interval = models.PositiveIntegerField(
        default=vldt.OFFLINE_MIN_INTERVAL,
        validators=[vldt.offline_notification_interval]
    )
    notification_channel = models.IntegerField(
        choices=UserNotificationChoice.to_choices(),
        default=UserNotificationChoice.UNSET
    )

    is_online = models.BooleanField(default=False)
    last_heartbeat = models.DateTimeField(auto_now=True)

    email = models.EmailField(null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    bark = models.CharField(max_length=100, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    salt = models.CharField(max_length=vldt.SALT_MAX_LENGTH)

    is_deleted = models.BooleanField(default=False)

    @classmethod
    def get_class(cls, role):
        return HostUser if role == UserRoleChoice.HOST else GuestUser

    @classmethod
    def index(cls, user_id):
        users = cls.objects.filter(id=user_id, is_deleted=False)
        if not users.exists():
            raise UserErrors.NOT_EXISTS(attr=_('user id'), value=user_id)
        user = users.first()
        return user.specify()

    def specify(self):
        if type(self) is BaseUser:
            return self.get_class(self.role).index(self.id)
        return self

    def _dictify_user_id(self):
        return self.id

    def _dictify_last_heartbeat(self):
        return self.last_heartbeat.timestamp()

    def _dictify_email_verified_at(self):
        if self.email_verified_at is None:
            return None
        return self.email_verified_at.timestamp()

    def jwt_json(self):
        return self.tiny_json()

    @classmethod
    def jwt_login(cls, data):
        return cls.index(data['user_id'])

    def heartbeat(self):
        self.last_heartbeat = timezone.now()
        self.save()

    def get_chat_list(self):
        raise NotImplementedError

    @property
    def guest(self):
        return self.role == UserRoleChoice.GUEST

    @property
    def verified(self):
        return self.account_level == UserAccountLevelChoice.VERIFIED

    @property
    def space_host(self):
        user = self.specify()
        if user.role == UserRoleChoice.HOST:
            return user
        return user.host

    @property
    def space(self):
        return Space.get_by_host(self.space_host)

    def json(self):
        return self.dictify(
            'name',
            'user_id',
            'is_alive',
            'guest',
            'verified',
            'last_heartbeat',
            'email_verified_at',
        )

    def tiny_json(self):
        return self.dictify('name', 'user_id')

    @property
    def is_alive(self):
        current_time = timezone.now()
        return (current_time - self.last_heartbeat).seconds < self.vldt.OFFLINE_MIN_INTERVAL * 60


class Config(models.Model):
    vldt = ConfigValidator

    key = models.CharField(
        max_length=vldt.MAX_KEY_LENGTH,
        unique=True,
        validators=[vldt.key],
    )
    value = models.CharField(
        max_length=vldt.MAX_VALUE_LENGTH,
        validators=[vldt.value],
    )

    @classmethod
    def get_config_by_key(cls, key):
        try:
            return cls.objects.get(key=key)
        except cls.DoesNotExist as err:
            raise ConfigErrors.NOT_FOUND(details=err)

    @classmethod
    def get_value_by_key(cls, key, default=None):
        try:
            return cls.get_config_by_key(key).value
        except Exception:
            return default

    @classmethod
    def update_value(cls, key, value):
        try:
            config = cls.get_config_by_key(key)
            config.value = value
            config.save(update_fields=['value'])
        except Error as e:
            if e == ConfigErrors.NOT_FOUND:
                try:
                    config = cls(key=key, value=value)
                    config.save()
                except Exception as err:
                    raise ConfigErrors.CREATE(details=err)
            else:
                raise e
        except Exception as err:
            raise ConfigErrors.CREATE(details=err)


class ConfigInstance:
    NOTIFICATOR_SDK_PATH = 'NOTIFICATOR_SDK_PATH'
    NOTIFICATOR_NAME = 'NOTIFICATOR_NAME'
    NOTIFICATOR_TOKEN = 'NOTIFICATOR_TOKEN'
    NOTIFICATOR_HOST = 'NOTIFICATOR_HOST'
    NOTIFICATOR_TIMEOUT = 'NOTIFICATOR_TIMEOUT'


CI = ConfigInstance


class HostUser(BaseUser):
    vldt = BaseUserValidator

    name = models.CharField(
        max_length=vldt.NAME_MAX_LENGTH,
        unique=True,
        validators=[vldt.name]
    )
    lower_name = models.CharField(
        max_length=vldt.NAME_MAX_LENGTH,
        unique=True,
    )
    password = models.CharField(max_length=vldt.PASSWORD_MAX_LENGTH, validators=[vldt.password])
    description = models.CharField(max_length=vldt.DESCRIPTION_MAX_LENGTH, null=True, blank=True)
    subdomain = models.CharField(max_length=vldt.SUBDOMAIN_MAX_LENGTH, null=True, blank=True, validators=[vldt.subdomain], unique=True)

    @classmethod
    def create(cls, name, password, subdomain=None):
        if cls.objects.filter(lower_name=name.lower()).exists():
            raise UserErrors.EXISTS
        salt = function.get_salt(length=cls.vldt.SALT_MAX_LENGTH)
        password = function.hash_password(password, salt)
        if subdomain is None:
            subdomain = cls._generate_subdomain()
        else:
            subdomain = subdomain.strip().lower()
            cls.vldt.subdomain(subdomain)
            if validators.is_reserved_subdomain(subdomain):
                raise UserErrors.SUBDOMAIN_RESERVED
            if cls.objects.filter(subdomain=subdomain).exists():
                raise UserErrors.SUBDOMAIN_TAKEN
        return cls.objects.create(
            name=name,
            lower_name=name.lower(),
            password=password,
            salt=salt,
            role=UserRoleChoice.HOST,
            account_level=UserAccountLevelChoice.VERIFIED,
            subdomain=subdomain,
        )

    @classmethod
    def login(cls, name, password):
        users = cls.objects.filter(lower_name=name.lower())
        if not users.exists():
            raise UserErrors.NOT_EXISTS(attr=_('name'), value=name)
        user = users.first()
        if not function.verify_password(password, user.salt, user.password):
            raise UserErrors.PASSWORD_ERROR
        return user

    @classmethod
    def get(cls, name):
        users = cls.objects.filter(lower_name=name.lower())
        if not users.exists():
            raise UserErrors.NOT_EXISTS(attr=_('name'), value=name)
        return users.first()

    @classmethod
    def get_by_subdomain(cls, subdomain):
        users = cls.objects.filter(subdomain=subdomain)
        if not users.exists():
            raise UserErrors.NOT_EXISTS(attr=_('subdomain'), value=subdomain)
        return users.first()

    @classmethod
    def is_subdomain_available(cls, subdomain, exclude_user_id=None):
        if validators.is_reserved_subdomain(subdomain):
            return False
        query = cls.objects.filter(subdomain=subdomain)
        if exclude_user_id is not None:
            query = query.exclude(id=exclude_user_id)
        return not query.exists()

    @classmethod
    def _generate_subdomain(cls, length=None, max_attempts=10):
        length = length or cls.vldt.SUBDOMAIN_RANDOM_LENGTH
        for _ in range(max_attempts):
            candidate = function.get_subdomain(length=length)
            if validators.is_reserved_subdomain(candidate):
                continue
            if not cls.objects.filter(subdomain=candidate).exists():
                return candidate
        raise UserErrors.SUBDOMAIN_TAKEN

    def set_subdomain(self, subdomain):
        subdomain = subdomain.lower()
        self.vldt.subdomain(subdomain)
        if validators.is_reserved_subdomain(subdomain):
            raise UserErrors.SUBDOMAIN_RESERVED
        if not self.is_subdomain_available(subdomain, exclude_user_id=self.id):
            raise UserErrors.SUBDOMAIN_TAKEN
        self.subdomain = subdomain
        self.save(update_fields=['subdomain'])
        return self

    def json(self):
        return self.dictify(
            'name',
            'user_id',
            'is_alive',
            'guest',
            'verified',
            'description',
            'last_heartbeat',
            'email_verified_at',
            'subdomain',
        )

    def jwt_json(self):
        return self.dictify('name', 'user_id', 'subdomain')


class GuestUser(BaseUser):
    vldt = BaseUserValidator

    class Meta:
        unique_together = ('lower_name', 'host')

    host = models.ForeignKey(
        HostUser,
        on_delete=models.CASCADE,
        related_name='host',
    )

    password = models.CharField(
        max_length=vldt.PASSWORD_MAX_LENGTH,
        null=True,
        blank=True,
        validators=[vldt.password]
    )

    name = models.CharField(max_length=vldt.NAME_MAX_LENGTH, validators=[vldt.name])
    lower_name = models.CharField(max_length=vldt.NAME_MAX_LENGTH)

    @classmethod
    def create(cls, name, host):
        if host.lower_name == name.lower():
            raise UserErrors.EXISTS
        if cls.objects.filter(lower_name=name.lower(), host=host).exists():
            raise UserErrors.EXISTS
        salt = function.get_salt(length=cls.vldt.SALT_MAX_LENGTH)
        guest = cls.objects.create(
            name=name,
            lower_name=name.lower(),
            host=host,
            salt=salt,
            role=UserRoleChoice.GUEST,
            account_level=UserAccountLevelChoice.BASIC,
        )
        Friendship.create_or_get(host, guest)
        return guest

    def set_password(self, password, save=True):
        if not password:
            return self

        self.password = function.hash_password(password, self.salt)
        if save:
            self.save(update_fields=['password'])
        return self

    def verify_email_and_upgrade(self, email, password):
        self.set_password(password, save=False)
        self.email = email
        self.email_verified_at = timezone.now()
        self.account_level = UserAccountLevelChoice.VERIFIED
        self.save(update_fields=['password', 'email', 'email_verified_at', 'account_level'])
        return self

    @classmethod
    def login(cls, name, password, host):
        users = cls.objects.filter(lower_name=name.lower(), host=host)
        if not users.exists():
            return cls.create(name, host).set_password(password)

        user = users.first()
        if user.is_deleted:
            raise UserErrors.GUEST_DELETED
        if user.password:
            if not password:
                raise UserErrors.PASSWORD_REQUIRED
            if not function.verify_password(password, user.salt, user.password):
                raise UserErrors.PASSWORD_ERROR
            return user

        if password:
            user.set_password(password)
        return user

    @classmethod
    def get(cls, name, host):
        users = cls.objects.filter(lower_name=name.lower(), host=host)
        if not users.exists():
            return None
        return users.first()


class Space(models.Model, Dictify):
    name = models.CharField(max_length=BaseUserValidator.NAME_MAX_LENGTH)
    slug = models.CharField(
        max_length=BaseUserValidator.SUBDOMAIN_MAX_LENGTH,
        unique=True,
        db_index=True,
        validators=[BaseUserValidator.subdomain],
    )
    official_user = models.OneToOneField(
        HostUser,
        on_delete=models.CASCADE,
        related_name='space',
    )
    group_square_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def get_by_slug(cls, slug):
        slug = slug.strip().lower()
        spaces = cls.objects.filter(slug=slug)
        if not spaces.exists():
            host = HostUser.objects.filter(subdomain=slug).first()
            if host is None:
                raise UserErrors.NOT_EXISTS(attr=_('space slug'), value=slug)
            return cls.objects.create(
                name=host.name,
                slug=slug,
                official_user=host,
            )
        return spaces.first()

    @classmethod
    def get_by_host(cls, host):
        spaces = cls.objects.filter(official_user=host)
        if not spaces.exists():
            slug = host.subdomain or HostUser._generate_subdomain()
            if host.subdomain != slug:
                host.subdomain = slug
                host.save(update_fields=['subdomain'])
            return cls.objects.create(
                name=host.name,
                slug=slug,
                official_user=host,
            )
        return spaces.first()

    @classmethod
    def create(cls, name, slug, official_name, password):
        slug = slug.strip().lower()
        BaseUserValidator.subdomain(slug)
        if validators.is_reserved_subdomain(slug):
            raise UserErrors.SUBDOMAIN_RESERVED
        if cls.objects.filter(slug=slug).exists():
            raise UserErrors.SUBDOMAIN_TAKEN

        with transaction.atomic():
            host = HostUser.create(
                name=official_name,
                password=password,
                subdomain=slug,
            )
            return cls.objects.create(
                name=name,
                slug=slug,
                official_user=host,
            )

    def _dictify_created_at(self):
        return self.created_at.timestamp()

    def _dictify_official_user(self):
        return self.official_user.json()

    def json(self):
        return self.dictify(
            'id->space_id',
            'name',
            'slug',
            'group_square_enabled',
            'official_user',
            'created_at',
        )


class Friendship(models.Model, Dictify):
    host = models.ForeignKey(
        HostUser,
        on_delete=models.CASCADE,
        related_name='friendships',
        db_index=True,
    )
    user_1 = models.ForeignKey(
        BaseUser,
        on_delete=models.CASCADE,
        related_name='friendships_as_user_1',
    )
    user_2 = models.ForeignKey(
        BaseUser,
        on_delete=models.CASCADE,
        related_name='friendships_as_user_2',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('host', 'user_1', 'user_2')

    @classmethod
    def _pair(cls, user_a: BaseUser, user_b: BaseUser):
        user_a = user_a.specify()
        user_b = user_b.specify()
        if user_a.id == user_b.id:
            raise UserErrors.FRIEND_INVALID
        host_a = user_a.space_host
        host_b = user_b.space_host
        if host_a.id != host_b.id:
            raise UserErrors.SPACE_FORBIDDEN
        return host_a, (user_a, user_b) if user_a.id < user_b.id else (user_b, user_a)

    @classmethod
    def create_or_get(cls, user_a: BaseUser, user_b: BaseUser):
        host, (user_1, user_2) = cls._pair(user_a, user_b)
        friendship, _created = cls.objects.get_or_create(
            host=host,
            user_1=user_1,
            user_2=user_2,
        )
        return friendship

    @classmethod
    def exists_between(cls, user_a: BaseUser, user_b: BaseUser):
        host, (user_1, user_2) = cls._pair(user_a, user_b)
        return cls.objects.filter(host=host, user_1=user_1, user_2=user_2).exists()

    @classmethod
    def friends_of(cls, user: BaseUser):
        user = user.specify()
        left = cls.objects.filter(user_1=user).select_related('user_2')
        right = cls.objects.filter(user_2=user).select_related('user_1')
        friends = [item.user_2.specify() for item in left]
        friends.extend(item.user_1.specify() for item in right)
        return friends


class FriendRequestStatusChoice(Choice):
    PENDING = 0
    ACCEPTED = 1
    REJECTED = 2
    CANCELED = 3


class FriendRequest(models.Model, Dictify):
    host = models.ForeignKey(
        HostUser,
        on_delete=models.CASCADE,
        related_name='friend_requests',
        db_index=True,
    )
    from_user = models.ForeignKey(
        BaseUser,
        on_delete=models.CASCADE,
        related_name='sent_friend_requests',
    )
    to_user = models.ForeignKey(
        BaseUser,
        on_delete=models.CASCADE,
        related_name='received_friend_requests',
    )
    status = models.IntegerField(
        choices=FriendRequestStatusChoice.to_choices(),
        default=FriendRequestStatusChoice.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def index(cls, request_id):
        items = cls.objects.filter(id=request_id)
        if not items.exists():
            raise UserErrors.NOT_EXISTS(attr=_('friend request'), value=request_id)
        return items.first()

    @classmethod
    def create_request(cls, from_user: BaseUser, to_user: BaseUser):
        from_user = from_user.specify()
        to_user = to_user.specify()
        if from_user.id == to_user.id:
            raise UserErrors.FRIEND_INVALID
        if from_user.guest and not from_user.verified:
            raise UserErrors.FRIEND_REQUEST_FORBIDDEN

        host, (user_1, user_2) = Friendship._pair(from_user, to_user)
        if Friendship.exists_between(from_user, to_user):
            raise UserErrors.FRIEND_ALREADY
        duplicated = cls.objects.filter(
            host=host,
            status=FriendRequestStatusChoice.PENDING,
        ).filter(
            models.Q(from_user=user_1, to_user=user_2) |
            models.Q(from_user=user_2, to_user=user_1)
        ).exists()
        if duplicated:
            raise UserErrors.FRIEND_REQUEST_EXISTS

        request_obj = cls.objects.create(
            host=host,
            from_user=from_user,
            to_user=to_user,
            status=FriendRequestStatusChoice.PENDING,
        )
        NotificationEvent.emit_system_event(
            user=to_user,
            actor=from_user,
            payload=dict(
                kind='friend_request',
                request_id=request_obj.id,
                from_user=from_user.tiny_json(),
            ),
        )
        return request_obj

    def accept(self, user: BaseUser):
        user = user.specify()
        if user.id != self.to_user_id:
            raise UserErrors.FRIEND_REQUEST_FORBIDDEN
        if self.status != FriendRequestStatusChoice.PENDING:
            raise UserErrors.FRIEND_REQUEST_CLOSED
        with transaction.atomic():
            self.status = FriendRequestStatusChoice.ACCEPTED
            self.save(update_fields=['status'])
            Friendship.create_or_get(self.from_user, self.to_user)
            NotificationEvent.emit_system_event(
                user=self.from_user,
                actor=self.to_user,
                payload=dict(
                    kind='friend_request_accepted',
                    request_id=self.id,
                    by_user=self.to_user.tiny_json(),
                ),
            )
        return self

    def reject(self, user: BaseUser):
        user = user.specify()
        if user.id != self.to_user_id:
            raise UserErrors.FRIEND_REQUEST_FORBIDDEN
        if self.status != FriendRequestStatusChoice.PENDING:
            raise UserErrors.FRIEND_REQUEST_CLOSED
        self.status = FriendRequestStatusChoice.REJECTED
        self.save(update_fields=['status'])
        return self

    def _dictify_created_at(self):
        return self.created_at.timestamp()

    def _dictify_updated_at(self):
        return self.updated_at.timestamp()

    def _dictify_from_user(self):
        return self.from_user.specify().tiny_json()

    def _dictify_to_user(self):
        return self.to_user.specify().tiny_json()

    def json(self):
        return self.dictify(
            'id->request_id',
            'status',
            'from_user',
            'to_user',
            'created_at',
            'updated_at',
        )


class RefreshToken(models.Model):
    user = models.ForeignKey(
        BaseUser,
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


class EmailVerificationCode(models.Model):
    CODE_LENGTH = 6
    EXPIRE_SECONDS = 10 * 60

    user = models.ForeignKey(BaseUser, on_delete=models.CASCADE, related_name='email_verification_codes')
    email = models.EmailField()
    code = models.CharField(max_length=CODE_LENGTH, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def issue(cls, user: BaseUser, email: str):
        user = user.specify()
        email = (email or '').strip().lower()
        now = timezone.now()
        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
        code = get_random_string(cls.CODE_LENGTH, allowed_chars='0123456789')
        return cls.objects.create(
            user=user,
            email=email,
            code=code,
            expires_at=now + datetime.timedelta(seconds=cls.EXPIRE_SECONDS),
        )

    @classmethod
    def verify(cls, user: BaseUser, email: str, code: str):
        user = user.specify()
        email = (email or '').strip().lower()
        code = (code or '').strip()
        item = cls.objects.filter(
            user=user,
            email=email,
            code=code,
            used_at__isnull=True,
        ).order_by('-created_at').first()
        if item is None:
            raise UserErrors.EMAIL_CODE_INVALID
        if item.expires_at <= timezone.now():
            raise UserErrors.EMAIL_CODE_EXPIRED
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


class NotificationPreference(models.Model, Dictify):
    CHANNEL_DEFAULT_THRESHOLDS = {
        UserNotificationChoice.EMAIL: 30,
        UserNotificationChoice.SMS: 15,
        UserNotificationChoice.BARK: 5,
    }

    user = models.ForeignKey(
        BaseUser,
        on_delete=models.CASCADE,
        related_name='notification_preferences',
    )
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
    def _default_enabled(cls, user: BaseUser, channel: int):
        user = user.specify()
        if channel == UserNotificationChoice.EMAIL:
            return bool(user.email) and user.verified
        return False

    @classmethod
    def _default_threshold(cls, channel: int):
        return cls.CHANNEL_DEFAULT_THRESHOLDS.get(channel, 30)

    @classmethod
    def ensure_defaults(cls, user: BaseUser):
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
    def set_preference(cls, user: BaseUser, channel: int, enabled=None, offline_threshold_minutes=None):
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


class NotificationEvent(models.Model, Dictify):
    host = models.ForeignKey(
        HostUser,
        on_delete=models.CASCADE,
        related_name='notification_events',
        db_index=True,
    )
    user = models.ForeignKey(
        BaseUser,
        on_delete=models.CASCADE,
        related_name='notification_events',
        db_index=True,
    )
    actor = models.ForeignKey(
        BaseUser,
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
        actor_name = None
        if self.actor_id:
            actor_name = self.actor.specify().name

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
    def _message_recipients(cls, chat, actor: BaseUser):
        from Chat.models import GroupChat, SingleChat

        chat = chat.specify()
        if isinstance(chat, SingleChat):
            users = [chat.host, chat.guest]
        elif isinstance(chat, GroupChat):
            users = [chat.host, *list(chat.guests.all())]
        else:
            users = []
        return [user for user in users if user.id != actor.id and not user.is_deleted]

    @classmethod
    def _message_event_type(cls, chat):
        from Chat.models import GroupChat

        chat = chat.specify()
        if isinstance(chat, GroupChat):
            return NotificationEventTypeChoice.GROUP_MESSAGE
        return NotificationEventTypeChoice.DIRECT_MESSAGE

    @classmethod
    def emit_message_notifications(cls, message, actor: BaseUser):
        event_type = cls._message_event_type(message.chat)
        payload = dict(
            chat_id=message.chat_id,
            message_id=message.id,
            message_type=message.type,
            content=message.content,
        )
        created_events = []
        for user in cls._message_recipients(message.chat, actor):
            event = cls.objects.create(
                host=user.space_host,
                user=user,
                actor=actor,
                event_type=event_type,
                payload=payload,
            )
            NotificationDelivery.enqueue_for_event(event)
            created_events.append(event)
        return created_events

    @classmethod
    def emit_system_event(cls, user: BaseUser, actor: BaseUser, payload: dict):
        user = user.specify()
        actor = actor.specify() if actor else None
        event = cls.objects.create(
            host=user.space_host,
            user=user,
            actor=actor,
            event_type=NotificationEventTypeChoice.SYSTEM,
            payload=payload or {},
        )
        NotificationDelivery.enqueue_for_event(event)
        return event


class NotificationDelivery(models.Model, Dictify):
    event = models.ForeignKey(
        NotificationEvent,
        on_delete=models.CASCADE,
        related_name='deliveries',
        db_index=True,
    )
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
    def _channel_available(cls, user: BaseUser, channel: int):
        user = user.specify()
        if channel == UserNotificationChoice.EMAIL:
            return bool(user.email) and user.verified
        if channel == UserNotificationChoice.SMS:
            return bool(user.phone)
        if channel == UserNotificationChoice.BARK:
            return bool(user.bark)
        return False

    @classmethod
    def _channel_target(cls, user: BaseUser, channel: int):
        user = user.specify()
        if channel == UserNotificationChoice.EMAIL:
            return user.email
        if channel == UserNotificationChoice.SMS:
            return user.phone
        if channel == UserNotificationChoice.BARK:
            return user.bark
        return None

    @classmethod
    def _offline_threshold_reached(cls, user: BaseUser, threshold_minutes: int):
        user = user.specify()
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
            from User.notificator_client import send
            ok, detail = send(
                channel=self.channel,
                target=target,
                title=title,
                body=body,
                recipient_name=self.event.user.specify().name,
            )
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
    def process_pending(cls, user: BaseUser = None, limit: int = 200):
        query = cls.objects.filter(status=NotificationDeliveryStatusChoice.PENDING).select_related('event', 'event__user')
        if user is not None:
            query = query.filter(event__user=user.specify())
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
