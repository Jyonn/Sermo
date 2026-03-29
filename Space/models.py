import datetime

from django.utils import timezone
from django.utils.crypto import get_random_string

import smartdjango.models as models
from smartdjango import Choice

from Space.validators import SpaceValidator, SpaceErrors


class Space(models.Model):
    vldt = SpaceValidator

    OFFICIAL_NAME = 'Official'

    name = models.CharField(max_length=vldt.NAME_MAX_LENGTH)
    slug = models.CharField(
        max_length=vldt.SLUG_MAX_LENGTH,
        unique=True,
        db_index=True,
        validators=[vldt.slug],
    )
    email = models.EmailField(unique=True, db_index=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    official_user = models.OneToOneField(
        'User.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='official_space',
    )
    group_square_enabled = models.BooleanField(default=False)
    member_limit = models.PositiveIntegerField(null=True, blank=True, default=None)
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
        if cls.objects.filter(email=email).exists():
            raise SpaceErrors.EMAIL_TAKEN

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

    def _dictify_official_user(self):
        if self.official_user_id is None:
            return None
        return self.official_user.tiny_json()

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

    def ensure_member_limit_available(self):
        if self.member_limit is None:
            return self
        if self.active_member_count() >= self.member_limit:
            raise SpaceErrors.MEMBER_LIMIT_REACHED
        return self

    def set_admin_settings(self, name, group_square_enabled, member_limit):
        normalized_name = self.vldt.name(name)
        normalized_member_limit = self.vldt.member_limit(member_limit)
        current_member_count = self.active_member_count()
        if normalized_member_limit is not None and normalized_member_limit < current_member_count:
            raise SpaceErrors.MEMBER_LIMIT_TOO_LOW

        self.name = normalized_name
        self.group_square_enabled = bool(group_square_enabled)
        self.member_limit = normalized_member_limit
        self.save(update_fields=['name', 'group_square_enabled', 'member_limit'])
        return self

    def json(self):
        return self.jsonl()

    def jsonl(self):
        return self.dictify(
            'id->space_id',
            'name',
            'slug',
            'official_user',
            'group_square_enabled',
            'member_limit',
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
            'member_limit',
            'created_at',
        )


class SpaceEmailCodePurposeChoice(Choice):
    REGISTER = 1
    LOGIN = 2


class SpaceEmailVerificationCode(models.Model):
    CODE_LENGTH = 6
    EXPIRE_SECONDS = 10 * 60

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
        query = cls.objects.filter(
            email=email,
            purpose=purpose,
            used_at__isnull=True,
        )
        if space is None:
            query = query.filter(space__isnull=True)
        else:
            query = query.filter(space=space)
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
