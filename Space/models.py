import datetime
import threading

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

import smartdjango.models as models
from smartdjango import Choice

from Space.validators import SpaceValidator, SpaceErrors


def default_level_names():
    return [
        '初见', '起言', '同频', '渐熟', '热聊', '成群',
        '入浪', '逐潮', '回响', '共鸣', '风生', '云起',
        '浪涌', '潮生', '星聚', '盛放', '无界', '尽兴',
    ]


class SpaceNormalizers:
    @staticmethod
    def name(value):
        return (value or '').strip()

    @staticmethod
    def slug(value):
        return (value or '').strip().lower()

    @staticmethod
    def email(value):
        return (value or '').strip().lower()


class Space(models.Model):
    normalizers = SpaceNormalizers
    validators = SpaceValidator
    vldt = SpaceValidator

    OFFICIAL_NAME = 'Official'

    name = models.CharField(max_length=vldt.NAME_MAX_LENGTH)
    slug = models.CharField(
        max_length=vldt.SLUG_MAX_LENGTH,
        unique=True,
        db_index=True,
        validators=[vldt.slug],
    )
    email = models.EmailField(db_index=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    admin_phone = models.CharField(max_length=32, blank=True, default='')
    admin_phone_verified_at = models.DateTimeField(null=True, blank=True)
    identity_document_key = models.CharField(max_length=255, blank=True, default='')
    identity_submitted_at = models.DateTimeField(null=True, blank=True)
    identity_verified_at = models.DateTimeField(null=True, blank=True)
    capacity_notice_tier = models.PositiveSmallIntegerField(default=0)
    official_user = models.OneToOneField(
        'User.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='official_space',
    )
    group_square_enabled = models.BooleanField(default=False)
    chat_enabled = models.BooleanField(default=True)
    submission_enabled = models.BooleanField(default=False)
    square_explore_enabled = models.BooleanField(default=True)
    unverified_group_policy = models.PositiveSmallIntegerField(default=2)
    member_limit = models.PositiveIntegerField(null=True, blank=True, default=None)
    level_names = models.JSONField(default=default_level_names)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def get_by_slug(cls, slug):
        slug = (slug or '').strip().lower()
        try:
            return cls.objects.get(slug=slug)
        except cls.DoesNotExist:
            raise SpaceErrors.NOT_EXISTS(attr='slug', value=slug)

    @classmethod
    def index(cls, space_id):
        try:
            return cls.objects.get(id=space_id)
        except cls.DoesNotExist:
            raise SpaceErrors.NOT_EXISTS(attr='space_id', value=space_id)

    @classmethod
    def create(cls, name, slug, email, code, language):
        slug = (slug or '').strip().lower()
        email = (email or '').strip().lower()
        name = cls.vldt.name(name)
        cls.vldt.slug(slug)
        if cls.vldt.reserved_slug(slug):
            raise SpaceErrors.SLUG_RESERVED
        if cls.objects.filter(slug=slug).exists():
            raise SpaceErrors.SLUG_TAKEN
        cls.require_email_creation_available(email)

        SpaceEmailVerificationCode.verify(
            email=email,
            code=code,
            purpose=SpaceEmailCodePurposeChoice.REGISTER,
            space=None,
        )
        space = cls.objects.create(
            name=name,
            slug=slug,
            email=email,
            email_verified_at=timezone.now(),
        )
        space.ensure_official_user(language=language)
        return space

    @classmethod
    def require_email_creation_available(cls, email):
        normalized = (email or '').strip().lower()
        if cls.objects.filter(email=normalized, admin_phone_verified_at__isnull=True).exists():
            raise SpaceErrors.EMAIL_TRIAL_SPACE_EXISTS
        return normalized

    @classmethod
    def login_by_email_code(cls, slug, email, code):
        space = cls.get_by_slug(slug)
        email = (email or space.email or '').strip().lower()
        if space.email != email:
            raise SpaceErrors.EMAIL_MISMATCH
        SpaceEmailVerificationCode.verify(
            email=email,
            code=code,
            purpose=SpaceEmailCodePurposeChoice.LOGIN,
            space=space,
        )
        return space

    def _dictify_created_at(self):
        return self.created_at.timestamp()

    def _dictify_email_verified_at(self):
        if self.email_verified_at is None:
            return None
        return self.email_verified_at.timestamp()

    def _dictify_admin_phone_verified_at(self):
        return self.admin_phone_verified_at.timestamp() if self.admin_phone_verified_at else None

    def _dictify_identity_submitted_at(self):
        return self.identity_submitted_at.timestamp() if self.identity_submitted_at else None

    def _dictify_identity_verified_at(self):
        return self.identity_verified_at.timestamp() if self.identity_verified_at else None

    def _dictify_official_user(self):
        if self.official_user_id is None:
            return None
        return self.official_user.tiny_json()

    def _dictify_group_square_enabled(self):
        return self.verification_tier != 'email' and self.group_square_enabled

    @classmethod
    def _build_official_name(cls, space):
        from User.models import User

        base = cls.OFFICIAL_NAME
        if not User.objects.filter(space=space, lower_name=base.lower()).exists():
            return base

        suffix = 2
        while True:
            suffix_str = str(suffix)
            room = max(1, User.vldt.NAME_MAX_LENGTH - len(suffix_str))
            candidate = f'{base[:room]}{suffix_str}'
            if not User.objects.filter(space=space, lower_name=candidate.lower()).exists():
                return candidate
            suffix += 1

    def ensure_official_user(self, language='zh-CN'):
        from User.models import User, UserRoleChoice

        if self.official_user:
            return self.official_user

        official_user = User.create(
            space=self,
            name=self._build_official_name(self),
            password=get_random_string(32),
            role=UserRoleChoice.OFFICIAL,
            language=language,
            email=self.email,
            verified=True,
        )

        self.official_user = official_user
        self.save(update_fields=['official_user'])
        return official_user

    def active_member_count(self):
        from User.models import User, UserRoleChoice

        return User.objects.filter(
            space=self,
            is_deleted=False,
            role=UserRoleChoice.MEMBER,
        ).count()

    @property
    def verification_tier(self):
        if self.identity_verified_at is not None:
            return 'identity'
        if self.admin_phone_verified_at is not None:
            return 'phone'
        return 'email'

    @property
    def tier_member_limit(self):
        return {'email': 5, 'phone': 100, 'identity': 500}[self.verification_tier]

    @property
    def effective_member_limit(self):
        return min(self.member_limit or self.tier_member_limit, self.tier_member_limit)

    def ensure_member_limit_available(self):
        if self.active_member_count() >= self.effective_member_limit:
            raise SpaceErrors.MEMBER_LIMIT_REACHED
        return self


    def notify_capacity_if_needed(self):
        count = self.active_member_count()
        limit = self.tier_member_limit
        tier_number = {'email': 1, 'phone': 2, 'identity': 3}[self.verification_tier]
        if count < int(limit * .8) or self.capacity_notice_tier >= tier_number:
            return
        claimed = type(self).objects.filter(
            id=self.id,
            capacity_notice_tier__lt=tier_number,
        ).update(capacity_notice_tier=tier_number)
        if not claimed:
            return
        self.capacity_notice_tier = tier_number
        threading.Thread(target=self._send_capacity_email, args=(count, limit), daemon=True).start()

    def _send_capacity_email(self, count, limit):
        from utils.notificator_integration import send_space_capacity_mail
        try:
            send_space_capacity_mail(self, count, limit)
        except Exception:
            # Capacity notifications must never interrupt member creation.
            pass

    def set_admin_settings(
            self, name, group_square_enabled, chat_enabled, square_explore_enabled,
            unverified_group_policy, member_limit, level_names=None, submission_enabled=None):
        normalized_name = self.vldt.name(name)
        normalized_member_limit = self.vldt.member_limit(member_limit)
        normalized_level_names = self.vldt.level_names(level_names or self.level_names)
        current_member_count = self.active_member_count()
        if normalized_member_limit is not None and normalized_member_limit < current_member_count:
            raise SpaceErrors.MEMBER_LIMIT_TOO_LOW
        if normalized_member_limit is not None and normalized_member_limit > self.tier_member_limit:
            raise SpaceErrors.MEMBER_LIMIT_TIER_EXCEEDED
        normalized_chat_enabled = bool(chat_enabled)
        normalized_square_enabled = bool(group_square_enabled)
        if self.verification_tier == 'email' and normalized_square_enabled:
            raise SpaceErrors.TIER_FEATURE_RESTRICTED
        if not normalized_chat_enabled and not normalized_square_enabled:
            raise SpaceErrors.MODULES_REQUIRED

        self.name = normalized_name
        self.group_square_enabled = normalized_square_enabled
        self.chat_enabled = normalized_chat_enabled
        if submission_enabled is not None:
            self.submission_enabled = bool(submission_enabled) and normalized_chat_enabled
        self.square_explore_enabled = bool(square_explore_enabled) and normalized_square_enabled
        self.unverified_group_policy = self.vldt.unverified_group_policy(unverified_group_policy)
        self.member_limit = normalized_member_limit
        self.level_names = normalized_level_names
        self.save(update_fields=[
            'name', 'group_square_enabled', 'chat_enabled', 'submission_enabled', 'square_explore_enabled',
            'unverified_group_policy', 'member_limit', 'level_names',
        ])
        return self

    def require_chat_enabled(self):
        if not self.chat_enabled:
            raise SpaceErrors.CHAT_DISABLED

    def require_submission_enabled(self):
        self.require_chat_enabled()
        if not self.submission_enabled:
            raise SpaceErrors.SUBMISSION_DISABLED

    def require_square_enabled(self, scope=None):
        if self.verification_tier == 'email' or not self.group_square_enabled:
            raise SpaceErrors.SQUARE_DISABLED
        if scope == 'all' and not self.square_explore_enabled:
            raise SpaceErrors.SQUARE_EXPLORE_DISABLED

    def require_group_join_allowed(self, user):
        user.require_capability('chat.group.join')

    def require_group_send_allowed(self, user):
        user.require_capability('chat.group.send')

    def json(self):
        return self.jsonl()

    def jsonl(self):
        return self.dictify(
            'id->space_id',
            'name',
            'slug',
            'official_user',
            'group_square_enabled',
            'chat_enabled',
            'submission_enabled',
            'square_explore_enabled',
            'unverified_group_policy',
            'member_limit',
            'verification_tier',
            'tier_member_limit',
            'level_names',
            'created_at',
        )

    def json_private(self):
        return self.dictify(
            'id->space_id',
            'name',
            'slug',
            'email',
            'email_verified_at',
            'official_user',
            'group_square_enabled',
            'chat_enabled',
            'submission_enabled',
            'square_explore_enabled',
            'unverified_group_policy',
            'member_limit',
            'verification_tier',
            'tier_member_limit',
            'admin_phone',
            'admin_phone_verified_at',
            'identity_submitted_at',
            'identity_verified_at',
            'level_names',
            'created_at',
        )


class SpaceOperator(models.Model):
    MAX_PER_SPACE = 5

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name='operators')
    user = models.OneToOneField('User.User', on_delete=models.CASCADE, related_name='space_operator')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'id')

    def json(self):
        return dict(
            operator_id=self.id,
            user=self.user.jsonl(),
            created_at=self.created_at.timestamp(),
        )


class SpaceEmailCodePurposeChoice(Choice):
    REGISTER = 1
    LOGIN = 2


class SpacePhoneVerificationCode(models.Model):
    CODE_LENGTH = 6
    EXPIRE_SECONDS = 10 * 60

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name='phone_codes')
    phone = models.CharField(max_length=32, db_index=True)
    code = models.CharField(max_length=CODE_LENGTH, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def issue(cls, space, phone):
        normalized = str(phone or '').strip()
        if not normalized:
            raise SpaceErrors.PHONE_REQUIRED
        if space.admin_phone_verified_at is not None:
            raise SpaceErrors.PHONE_ALREADY_VERIFIED
        now = timezone.now()
        cls.objects.filter(space=space, used_at__isnull=True).update(used_at=now)
        return cls.objects.create(
            space=space,
            phone=normalized,
            code=get_random_string(cls.CODE_LENGTH, allowed_chars='0123456789'),
            expires_at=now + datetime.timedelta(seconds=cls.EXPIRE_SECONDS),
        )

    @classmethod
    def verify(cls, space, phone, code):
        item = cls.objects.filter(
            space=space,
            phone=str(phone or '').strip(),
            code=str(code or '').strip(),
            used_at__isnull=True,
        ).order_by('-created_at').first()
        if item is None:
            raise SpaceErrors.PHONE_CODE_INVALID
        if item.expires_at <= timezone.now():
            raise SpaceErrors.PHONE_CODE_EXPIRED
        item.used_at = timezone.now()
        item.save(update_fields=['used_at'])
        space.admin_phone = item.phone
        space.admin_phone_verified_at = timezone.now()
        space.capacity_notice_tier = 0
        space.save(update_fields=['admin_phone', 'admin_phone_verified_at', 'capacity_notice_tier'])
        return space


class SpaceEmailVerificationCode(models.Model):
    CODE_LENGTH = 6
    EXPIRE_SECONDS = 10 * 60
    SEND_COOLDOWN_SECONDS = 60

    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name='email_codes',
        null=True,
        blank=True,
    )
    email = models.EmailField(db_index=True)
    purpose = models.IntegerField(
        choices=SpaceEmailCodePurposeChoice.to_choices(),
        db_index=True,
    )
    code = models.CharField(max_length=CODE_LENGTH, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def issue(cls, email: str, purpose: int, space: Space = None):
        email = (email or '').strip().lower()
        now = timezone.now()
        with transaction.atomic():
            if space is not None:
                Space.objects.select_for_update().only('id').get(id=space.id)
            query = cls.objects.select_for_update().filter(
                email=email,
                purpose=purpose,
                used_at__isnull=True,
            )
            if space is None:
                query = query.filter(space__isnull=True)
            else:
                query = query.filter(space=space)
            latest = query.order_by('-created_at').first()
            if latest and latest.created_at > now - datetime.timedelta(seconds=cls.SEND_COOLDOWN_SECONDS):
                raise SpaceErrors.EMAIL_CODE_TOO_FREQUENT
            query.update(used_at=now)

            code = get_random_string(cls.CODE_LENGTH, allowed_chars='0123456789')
            return cls.objects.create(
                space=space,
                email=email,
                purpose=purpose,
                code=code,
                expires_at=now + datetime.timedelta(seconds=cls.EXPIRE_SECONDS),
            )

    @classmethod
    def verify(cls, email: str, code: str, purpose: int, space: Space = None):
        email = (email or '').strip().lower()
        code = (code or '').strip()
        query = cls.objects.filter(
            email=email,
            purpose=purpose,
            code=code,
            used_at__isnull=True,
        ).order_by('-created_at')
        if space is None:
            query = query.filter(space__isnull=True)
        else:
            query = query.filter(space=space)
        item = query.first()
        if item is None:
            raise SpaceErrors.EMAIL_CODE_INVALID
        if item.expires_at <= timezone.now():
            raise SpaceErrors.EMAIL_CODE_EXPIRED
        item.used_at = timezone.now()
        item.save(update_fields=['used_at'])
        return item
