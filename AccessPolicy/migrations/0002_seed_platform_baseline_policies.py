from django.db import migrations


def atom(field, op='eq', value=True):
    return {'field': field, 'op': op, 'value': value}


def all_of(*expressions):
    return {'all': list(expressions)}


def any_of(*expressions):
    return {'any': list(expressions)}


def level(value):
    return atom('growth_level', 'gte', value)


BACKGROUND_LEVELS = {
    'default': 1, 'paper': 2, 'mint': 3, 'dusk': 4, 'comic': 5,
    'zen': 6, 'hero': 7, 'dragon': 8, 'bauhaus': 8, 'mosaic': 9,
    'tidepool': 9, 'forest': 10, 'desert': 10, 'sunrise': 11,
    'snowfield': 11, 'sakura': 12, 'midnight': 12, 'rain': 13,
    'galaxy': 13, 'aurora-sky': 14, 'linen': 14, 'terrazzo': 14,
    'blueprint': 15, 'newsprint': 15, 'hologram': 15, 'arcade': 16,
    'jazz': 16, 'spaceport': 17, 'candy': 17, 'noir-film': 18,
    'custom': 8,
}

BUBBLE_LEVELS = {
    'default': 1, 'comic': 2, 'typewriter': 4, 'sticker': 5,
    'zen': 6, 'newspaper': 7, 'toybrick': 8, 'hero': 9,
    'bauhaus': 10, 'receipt': 11, 'dragon': 12, 'mosaic': 13,
    'niko': 16, 'fufu': 17, 'baxian-lv': 18,
    'baxian-zhongli': 18, 'baxian-he': 18,
}

FRAME_LEVELS = {
    'none': 1, 'orbit': 2, 'polaroid': 3, 'camera': 4,
    'soundwave': 5, 'butterfly': 6, 'aurora': 7, 'moon': 8,
    'papercut': 9, 'comet': 10, 'snowfall': 11, 'portal': 12,
    'mechanical': 13, 'niko-run': 16, 'fufu-wave': 17,
}


def baseline_requirements():
    values = {
        'chat.message.send.image': level(2),
        'chat.message.send.audio': level(3),
        'chat.message.send.location': level(3),
        'chat.message.send.video': all_of(level(5), atom('space_phone_verified')),
        'chat.message.send.file': atom('space_phone_verified'),
        'chat.message.download.audio': level(8),
        'chat.group.join': any_of(atom('verified'), atom('unverified_group_policy', 'gte', 1)),
        'chat.group.send': any_of(atom('verified'), atom('unverified_group_policy', 'gte', 2)),
        'chat.group.create': all_of(level(4), atom('verified')),
        'chat.group.invite': all_of(level(4), atom('verified')),
        'chat.group.rename': level(5),
        'chat.reminder.online': level(7),
        'square.statement.publish': atom('verified'),
        'square.statement.publish.audio': level(6),
        'square.statement.publish.video': level(10),
        'square.interaction': atom('verified'),
        'contacts.search': atom('verified'),
        'contacts.friend_request': any_of(atom('verified'), atom('qr_invite')),
        'contacts.qr': atom('verified'),
        'menu.profile.avatar.custom': level(4),
        'menu.profile.nickname': level(5),
        'menu.profile.welcome': level(6),
        'menu.sticker.create': level(6),
        'menu.security.private_account': atom('phone_verified'),
        'menu.personalization.statement': atom('square_enabled'),
        'menu.personalization.bubble.use.vip': atom('permanent_vip'),
        'menu.personalization.frame.use.vip': atom('permanent_vip'),
        'menu.personalization.statement.use.vip': atom('permanent_vip'),
        'menu.personalization.statement.use.niko': any_of(level(16), atom('permanent_vip')),
        'menu.personalization.statement.use.fufu': any_of(level(16), atom('permanent_vip')),
    }
    for asset_key, required_level in BACKGROUND_LEVELS.items():
        values[f'menu.personalization.background.use.{asset_key}'] = level(required_level)
    for asset_key, required_level in BUBBLE_LEVELS.items():
        requirement = level(required_level)
        if asset_key in {'niko', 'fufu'}:
            requirement = any_of(requirement, atom('permanent_vip'))
        values[f'menu.personalization.bubble.use.{asset_key}'] = requirement
    for asset_key, required_level in FRAME_LEVELS.items():
        requirement = level(required_level)
        if asset_key in {'niko-run', 'fufu-wave'}:
            requirement = any_of(requirement, atom('permanent_vip'))
        values[f'menu.personalization.frame.use.{asset_key}'] = requirement
    return values


def seed_platform_baselines(apps, schema_editor):
    PlatformCapabilityPolicy = apps.get_model('AccessPolicy', 'PlatformCapabilityPolicy')
    for capability_key, requirement in baseline_requirements().items():
        PlatformCapabilityPolicy.objects.get_or_create(
            capability_key=capability_key,
            defaults={
                'requirement': requirement,
                'denial': {},
                'limits': {},
                'updated_by': 'system:migration',
            },
        )


def remove_seeded_baselines(apps, schema_editor):
    PlatformCapabilityPolicy = apps.get_model('AccessPolicy', 'PlatformCapabilityPolicy')
    PlatformCapabilityPolicy.objects.filter(updated_by='system:migration').delete()


class Migration(migrations.Migration):
    dependencies = [('AccessPolicy', '0001_initial')]

    operations = [
        migrations.RunPython(seed_platform_baselines, remove_seeded_baselines),
    ]
