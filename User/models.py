from diq import Dictify
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _
from smartdjango import Choice

from User.validators import BaseUserValidator, UserErrors
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


class BaseUser(models.Model, Dictify):
    vldt = BaseUserValidator

    role = models.IntegerField(choices=UserRoleChoice.to_choices())

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

    def json(self):
        return self.dictify('name', 'user_id', 'is_alive', 'guest', 'last_heartbeat')

    def tiny_json(self):
        return self.dictify('name', 'user_id')

    @property
    def is_alive(self):
        current_time = timezone.now()
        return (current_time - self.last_heartbeat).seconds < self.vldt.OFFLINE_MIN_INTERVAL * 60


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
    def create(cls, name, password):
        if cls.objects.filter(lower_name=name.lower()).exists():
            raise UserErrors.EXISTS
        salt = function.get_salt(length=cls.vldt.SALT_MAX_LENGTH)
        password = function.hash_password(password, salt)
        subdomain = cls._generate_subdomain()
        return cls.objects.create(
            name=name,
            lower_name=name.lower(),
            password=password,
            salt=salt,
            role=UserRoleChoice.HOST,
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
        return self.dictify('name', 'user_id', 'is_alive', 'guest', 'description', 'last_heartbeat', 'subdomain')

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
        if cls.objects.filter(lower_name=name.lower(), host=host).exists():
            raise UserErrors.EXISTS
        salt = function.get_salt(length=cls.vldt.SALT_MAX_LENGTH)
        return cls.objects.create(
            name=name,
            lower_name=name.lower(),
            host=host,
            salt=salt,
            role=UserRoleChoice.GUEST,
        )

    def set_password(self, password):
        if not password:
            return self

        self.password = function.hash_password(password, self.salt)
        self.save()
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
