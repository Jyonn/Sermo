from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _

from User.validators import BaseUserValidator, UserErrors
from utils import function
from utils.choice import Choice
from utils.jsonify import Jsonify


class UserNotificationChoice(Choice):
    UNSET = 0
    EMAIL = 1
    SMS = 2
    BARK = 3


class UserRoleChoice(Choice):
    HOST = 0
    GUEST = 1


class BaseUser(models.Model, Jsonify):
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

    @classmethod
    def get_class(cls, role):
        return HostUser if role == UserRoleChoice.HOST else GuestUser

    @classmethod
    def index(cls, user_id):
        users = cls.objects.filter(id=user_id)
        if not users.exists():
            raise UserErrors.NOT_EXISTS(attr=_('user id'), value=user_id)
        user = users.first()
        return user.specify()

    def specify(self):
        if type(self) is BaseUser:
            return self.get_class(self.role).index(self.id)
        return self

    def _jsonify_user_id(self):
        return self.id

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

    def tiny_json(self):
        return self.jsonify('name', 'user_id', 'is_alive', 'guest')

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

    @classmethod
    def create(cls, name, password):
        if cls.objects.filter(lower_name=name.lower()).exists():
            raise UserErrors.EXISTS
        salt = function.get_salt(length=cls.vldt.SALT_MAX_LENGTH)
        password = function.hash_password(password, salt)
        return cls.objects.create(
            name=name,
            lower_name=name.lower(),
            password=password,
            salt=salt,
            role=UserRoleChoice.HOST
        )

    @classmethod
    def login(cls, name, password):
        users = cls.objects.filter(lower_name=name.lower())
        if not users.exists():
            return cls.create(name, password)
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
        if not function.verify_password(password, user.salt, user.password):
            raise UserErrors.PASSWORD_ERROR
        return user
