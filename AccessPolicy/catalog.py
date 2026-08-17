from dataclasses import dataclass

from User.growth import CHAT_BACKGROUND_LEVELS, GROWTH_CAPABILITY_LEVELS, PERSONALIZATION_LEVELS


def atom(field, op='eq', value=True):
    return {'field': field, 'op': op, 'value': value}


def all_of(*expressions):
    values = [value for value in expressions if value]
    if not values:
        return {}
    if len(values) == 1:
        return values[0]
    return {'all': values}


def any_of(*expressions):
    values = [value for value in expressions if value]
    if not values:
        return {}
    if len(values) == 1:
        return values[0]
    return {'any': values}


def level(value):
    return atom('growth_level', 'gte', value)


@dataclass(frozen=True)
class CapabilityDefinition:
    key: str
    title: str
    title_en: str
    parent: str | None = None
    icon: str = 'lock_open'
    requirement: dict | None = None
    space_configurable: bool = True
    kind: str = 'gate'
    asset_key: str | None = None

    def payload(self):
        return {
            'key': self.key,
            'title': self.title,
            'title_en': self.title_en,
            'parent': self.parent,
            'icon': self.icon,
            'requirement': self.requirement or {},
            'space_configurable': self.space_configurable,
            'kind': self.kind,
            'asset_key': self.asset_key,
        }


_DEFINITIONS = []


def register(key, title, title_en, parent=None, icon='lock_open', requirement=None, space_configurable=True, kind='gate', asset_key=None):
    _DEFINITIONS.append(CapabilityDefinition(
        key=key,
        title=title,
        title_en=title_en,
        parent=parent,
        icon=icon,
        requirement=requirement or {},
        space_configurable=space_configurable,
        kind=kind,
        asset_key=asset_key,
    ))


register('chat', '聊天', 'Chat', icon='chat')
register('chat.message', '消息', 'Messages', 'chat', 'forum')
register('chat.message.send', '发送消息', 'Send messages', 'chat.message', 'send')
register('chat.message.send.text', '文字', 'Text', 'chat.message.send', 'text_fields')
register('chat.message.send.image', '图片', 'Images', 'chat.message.send', 'image', level(GROWTH_CAPABILITY_LEVELS['send_image']))
register('chat.message.send.audio', '语音', 'Voice', 'chat.message.send', 'mic', level(GROWTH_CAPABILITY_LEVELS['send_audio']))
register('chat.message.send.location', '位置', 'Location', 'chat.message.send', 'location_on', level(GROWTH_CAPABILITY_LEVELS['send_location']))
register('chat.message.send.video', '视频', 'Video', 'chat.message.send', 'videocam', all_of(level(GROWTH_CAPABILITY_LEVELS['send_video']), atom('space_phone_verified')))
register('chat.message.send.file', '文件', 'Files', 'chat.message.send', 'draft', requirement=atom('space_phone_verified'))
register('chat.message.send.sticker', '表情包', 'Stickers', 'chat.message.send', 'mood')
register('chat.message.download', '下载消息附件', 'Download attachments', 'chat.message', 'download')
register('chat.message.download.audio', '下载语音', 'Download voice', 'chat.message.download', 'audio_file', level(GROWTH_CAPABILITY_LEVELS['download_audio']))
register('chat.group', '群聊', 'Groups', 'chat', 'groups')
register('chat.group.join', '加入群聊', 'Join groups', 'chat.group', 'group_add', any_of(atom('verified'), atom('unverified_group_policy', 'gte', 1)))
register('chat.group.send', '在群聊发言', 'Send in groups', 'chat.group', 'forum', any_of(atom('verified'), atom('unverified_group_policy', 'gte', 2)))
register('chat.group.create', '创建群聊', 'Create groups', 'chat.group', 'group_add', all_of(level(GROWTH_CAPABILITY_LEVELS['create_group']), atom('verified')))
register('chat.group.invite', '邀请群成员', 'Invite group members', 'chat.group', 'person_add', all_of(level(GROWTH_CAPABILITY_LEVELS['invite_group_member']), atom('verified')))
register('chat.group.rename', '修改群名称', 'Rename groups', 'chat.group', 'edit', level(GROWTH_CAPABILITY_LEVELS['rename_group']))
register('chat.reminder', '聊天提醒', 'Chat reminders', 'chat', 'notifications')
register('chat.reminder.online', '好友上线提醒', 'Online reminders', 'chat.reminder', 'notifications_active', level(GROWTH_CAPABILITY_LEVELS['online_reminder']))

register('square', '广场', 'Square', icon='explore')
register('square.browse', '浏览发言', 'Browse statements', 'square', 'visibility')
register('square.explore', '探索', 'Explore', 'square.browse', 'travel_explore')
register('square.statement', '发言', 'Statements', 'square', 'campaign')
register('square.statement.publish', '发表发言', 'Publish statements', 'square.statement', 'edit_note', atom('verified'))
register('square.statement.publish.text', '文字发言', 'Text statements', 'square.statement.publish', 'text_fields')
register('square.statement.publish.image', '图片发言', 'Image statements', 'square.statement.publish', 'image')
register('square.statement.publish.audio', '语音发言', 'Voice statements', 'square.statement.publish', 'mic', level(6))
register('square.statement.publish.video', '视频发言', 'Video statements', 'square.statement.publish', 'videocam', level(8))
register('square.interaction', '互动', 'Interactions', 'square', 'favorite', atom('verified'))
register('square.interaction.like', '点赞', 'Like', 'square.interaction', 'favorite')
register('square.interaction.comment', '评论', 'Comment', 'square.interaction', 'comment')
register('square.interaction.reply', '回复评论', 'Reply to comments', 'square.interaction', 'reply')

register('contacts', '通讯', 'Contacts', icon='contacts')
register('contacts.search', '精确搜索用户', 'Exact user search', 'contacts', 'person_search', atom('verified'))
register('contacts.friend_request', '发送好友申请', 'Send friend requests', 'contacts', 'person_add', atom('verified'))
register('contacts.qr', '好友二维码', 'Friend QR code', 'contacts', 'qr_code_2', atom('verified'))

register('menu', '菜单', 'Menu', icon='menu')
register('menu.profile', '个人资料', 'Profile', 'menu', 'person')
register('menu.profile.avatar', '头像', 'Avatar', 'menu.profile', 'account_circle')
register('menu.profile.avatar.preset', '预设头像', 'Preset avatar', 'menu.profile.avatar', 'face')
register('menu.profile.avatar.custom', '自定义头像', 'Custom avatar', 'menu.profile.avatar', 'add_a_photo', level(GROWTH_CAPABILITY_LEVELS['custom_avatar']))
register('menu.profile.nickname', '修改昵称', 'Change nickname', 'menu.profile', 'edit', level(GROWTH_CAPABILITY_LEVELS['rename_nickname']))
register('menu.profile.welcome', '欢迎语', 'Welcome message', 'menu.profile', 'waving_hand', level(GROWTH_CAPABILITY_LEVELS['welcome_message']))
register('menu.sticker', '表情包管理', 'Sticker management', 'menu', 'mood')
register('menu.sticker.create', '制作表情包', 'Create stickers', 'menu.sticker', 'add_reaction', level(GROWTH_CAPABILITY_LEVELS['create_sticker']))
register('menu.security', '账号与安全', 'Account and security', 'menu', 'shield')
register('menu.security.private_account', '私密账号', 'Private account', 'menu.security', 'lock', atom('phone_verified'))
register('menu.personalization', '语言与个性化', 'Language and personalization', 'menu', 'palette')
register('menu.personalization.background', '聊天背景', 'Chat backgrounds', 'menu.personalization', 'wallpaper')
register('menu.personalization.bubble', '聊天气泡', 'Chat bubbles', 'menu.personalization', 'chat_bubble')
register('menu.personalization.frame', '头像框', 'Avatar frames', 'menu.personalization', 'portrait')
register('menu.personalization.statement', '发言外观', 'Statement appearance', 'menu.personalization', 'view_agenda', atom('square_enabled'))

for background, required_level in CHAT_BACKGROUND_LEVELS.items():
    register(
        f'menu.personalization.background.use.{background}', background, background,
        'menu.personalization.background', 'wallpaper', level(required_level), asset_key=background,
    )

for style, required_level in PERSONALIZATION_LEVELS['chat_bubble_style'].items():
    requirement = level(required_level)
    if style in {'niko', 'fufu'}:
        requirement = any_of(requirement, atom('permanent_vip'))
    register(
        f'menu.personalization.bubble.use.{style}', style, style,
        'menu.personalization.bubble', 'chat_bubble', requirement, asset_key=style,
    )

register('menu.personalization.bubble.use.vip', '永久 VIP', 'Permanent VIP', 'menu.personalization.bubble', 'workspace_premium', atom('permanent_vip'), asset_key='vip')
for style in ('city-jdz', 'city-shanghai', 'city-nyc', 'city-beijing'):
    register(f'menu.personalization.bubble.use.{style}', style, style, 'menu.personalization.bubble', 'location_city', asset_key=style)

for style, required_level in PERSONALIZATION_LEVELS['avatar_frame_style'].items():
    requirement = level(required_level)
    if style in {'niko-run', 'fufu-wave'}:
        requirement = any_of(requirement, atom('permanent_vip'))
    register(
        f'menu.personalization.frame.use.{style}', style, style,
        'menu.personalization.frame', 'portrait', requirement, asset_key=style,
    )
register('menu.personalization.frame.use.vip', '永久 VIP', 'Permanent VIP', 'menu.personalization.frame', 'workspace_premium', atom('permanent_vip'), asset_key='vip')

for style in ('default', 'editorial', 'mosaic', 'hero', 'comic', 'receipt'):
    register(f'menu.personalization.statement.use.{style}', style, style, 'menu.personalization.statement', 'view_agenda', asset_key=style)
register('menu.personalization.statement.use.vip', '永久 VIP', 'Permanent VIP', 'menu.personalization.statement', 'workspace_premium', atom('permanent_vip'), asset_key='vip')
for style in ('niko', 'fufu'):
    register(f'menu.personalization.statement.use.{style}', style, style, 'menu.personalization.statement', 'pets', any_of(level(16), atom('permanent_vip')), asset_key=style)


CAPABILITIES = {definition.key: definition for definition in _DEFINITIONS}

LEGACY_GROWTH_CAPABILITY_KEYS = {
    'send_image': 'chat.message.send.image',
    'send_audio': 'chat.message.send.audio',
    'send_location': 'chat.message.send.location',
    'send_video': 'chat.message.send.video',
    'custom_avatar': 'menu.profile.avatar.custom',
    'create_group': 'chat.group.create',
    'invite_group_member': 'chat.group.invite',
    'rename_nickname': 'menu.profile.nickname',
    'rename_group': 'chat.group.rename',
    'welcome_message': 'menu.profile.welcome',
    'plaza_greeting': 'menu.profile.welcome',
    'create_sticker': 'menu.sticker.create',
    'online_reminder': 'chat.reminder.online',
    'download_audio': 'chat.message.download.audio',
    'chat_background': 'menu.personalization.background',
    'custom_chat_background': 'menu.personalization.background.use.custom',
}


def get_capability(key):
    return CAPABILITIES.get(key)


def ancestors(key):
    result = []
    current = get_capability(key)
    while current is not None:
        result.append(current)
        current = get_capability(current.parent) if current.parent else None
    return list(reversed(result))


def catalog_payload():
    children = {}
    for definition in _DEFINITIONS:
        children.setdefault(definition.parent, []).append(definition)

    def serialize(definition):
        payload = definition.payload()
        payload['children'] = [serialize(child) for child in children.get(definition.key, [])]
        return payload

    return [serialize(definition) for definition in children.get(None, [])]
