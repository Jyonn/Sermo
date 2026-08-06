import re
from dataclasses import dataclass


GROWTH_THRESHOLDS = [
    0, 40, 100, 180, 280, 400, 550, 730, 950,
    1200, 1500, 1850, 2250, 2700, 3200, 3800, 4500, 5300,
]

DAILY_GROWTH_LIMIT = 40
WEEKLY_GROWTH_LIMIT = 100


@dataclass(frozen=True)
class GrowthEventRule:
    key: str
    category: str
    title: str
    points: int
    period: str = 'once'


EVENT_RULES = {
    rule.key: rule for rule in (
        GrowthEventRule('explore:avatar', 'explore', '更换头像', 20),
        GrowthEventRule('explore:image', 'explore', '首次发送图片', 20),
        GrowthEventRule('explore:audio', 'explore', '首次发送语音', 20),
        GrowthEventRule('explore:location', 'explore', '首次分享位置', 25),
        GrowthEventRule('explore:video', 'explore', '首次发送视频', 25),
        GrowthEventRule('explore:install_webapp', 'explore', '安装 WebApp', 40),
        GrowthEventRule('explore:first_personalization', 'explore', '首次使用个性化', 15),
        GrowthEventRule('explore:welcome', 'explore', '设置欢迎语', 15),
        GrowthEventRule('explore:create_group', 'explore', '首次创建群聊', 30),
        GrowthEventRule('explore:online_reminder', 'explore', '开启上线提醒', 15),
        GrowthEventRule('explore:custom_background', 'explore', '上传聊天背景', 25),
        GrowthEventRule('social:qr_friend', 'social', '二维码结识认证好友', 40),
        GrowthEventRule('security:password', 'security', '设置密码', 20),
        GrowthEventRule('security:email', 'security', '认证邮箱', 30),
        GrowthEventRule('security:phone', 'security', '绑定手机', 30),
        GrowthEventRule('security:bark', 'security', '绑定即时提醒', 15),
        GrowthEventRule('vip:permanent', 'vip', '获得永久 VIP', 500),
        GrowthEventRule('achievement:conversations_100', 'achievement', '完成 100 次有效会话', 80),
        GrowthEventRule('achievement:contacts_10', 'achievement', '与 10 位联系人有效互动', 60),
        GrowthEventRule('achievement:active_months_5', 'achievement', '连续活跃 5 个月', 120),
        GrowthEventRule('achievement:groups_5', 'achievement', '参与 5 个活跃群聊', 80),
        GrowthEventRule('achievement:weekly_goals_10', 'achievement', '完成 10 次周目标', 100),
    )
}

PERIODIC_EVENT_RULES = (
    (re.compile(r'^daily:first_verified_communication:\d{4}-\d{2}-\d{2}$'), GrowthEventRule('', 'daily', '今日首次有效沟通', 5, 'daily')),
    (re.compile(r'^daily:verified_conversation:\d{4}-\d{2}-\d{2}:[^:]+$'), GrowthEventRule('', 'daily', '完成有效双向会话', 8, 'daily')),
    (re.compile(r'^daily:verified_group:\d{4}-\d{2}-\d{2}:[^:]+$'), GrowthEventRule('', 'daily', '参与有效群聊', 6, 'daily')),
    (re.compile(r'^daily:verified_media:\d{4}-\d{2}-\d{2}:[^:]+:(image|audio|video|location)$'), GrowthEventRule('', 'daily', '使用有效媒体消息', 4, 'daily')),
    (re.compile(r'^daily:different_contact:\d{4}-\d{2}-\d{2}:[^:]+$'), GrowthEventRule('', 'daily', '与不同联系人沟通', 4, 'daily')),
    (re.compile(r'^daily:verified_reply:\d{4}-\d{2}-\d{2}:[^:]+$'), GrowthEventRule('', 'daily', '完成有效回复', 3, 'daily')),
    (re.compile(r'^weekly:active_3_days:\d{4}-W\d{2}$'), GrowthEventRule('', 'weekly', '本周活跃 3 天', 20, 'weekly')),
    (re.compile(r'^weekly:active_5_days:\d{4}-W\d{2}$'), GrowthEventRule('', 'weekly', '本周活跃 5 天', 25, 'weekly')),
    (re.compile(r'^weekly:contacts_3:\d{4}-W\d{2}$'), GrowthEventRule('', 'weekly', '本周联系 3 位好友', 20, 'weekly')),
    (re.compile(r'^weekly:groups_2:\d{4}-W\d{2}$'), GrowthEventRule('', 'weekly', '本周参与 2 个群聊', 15, 'weekly')),
    (re.compile(r'^weekly:media_3:\d{4}-W\d{2}$'), GrowthEventRule('', 'weekly', '本周使用 3 类媒体', 10, 'weekly')),
    (re.compile(r'^weekly:new_friend_conversation:\d{4}-W\d{2}:[^:]+$'), GrowthEventRule('', 'social', '与新认证好友首次双向沟通', 20, 'weekly')),
    (re.compile(r'^social:qr_friend:\d{4}-\d{2}:[12]$'), GrowthEventRule('', 'social', '二维码结识认证好友', 40, 'monthly')),
    (re.compile(r'^social:group_discussion:\d{4}-W\d{2}:[^:]+$'), GrowthEventRule('', 'social', '促成群聊有效讨论', 30, 'weekly')),
    (re.compile(r'^social:active_4_weeks:\d{4}-W\d{2}$'), GrowthEventRule('', 'social', '连续活跃 4 周', 60, 'once')),
)


def resolve_event_rule(event_key):
    rule = EVENT_RULES.get(event_key)
    if rule is not None:
        return rule
    for pattern, periodic_rule in PERIODIC_EVENT_RULES:
        if pattern.fullmatch(event_key):
            return periodic_rule
    return None


GROWTH_CAPABILITY_LEVELS = {
    'send_image': 2,
    'send_audio': 3,
    'send_location': 3,
    'custom_avatar': 4,
    'create_group': 4,
    'invite_group_member': 4,
    'rename_nickname': 5,
    'rename_group': 5,
    'send_video': 5,
    'welcome_message': 6,
    'plaza_greeting': 6,
    'online_reminder': 7,
    'download_audio': 8,
    'chat_background': 1,
    'custom_chat_background': 8,
    'custom_notification_message': 10,
}

CHAT_BACKGROUND_LEVELS = {
    'default': 1, 'paper': 2, 'mint': 3, 'dusk': 4, 'comic': 5,
    'zen': 6, 'hero': 7, 'dragon': 8, 'bauhaus': 8, 'mosaic': 9,
    'tidepool': 9, 'forest': 10, 'desert': 10, 'sunrise': 11,
    'snowfield': 11, 'sakura': 12, 'midnight': 12, 'rain': 13,
    'galaxy': 13, 'aurora-sky': 14, 'linen': 14, 'terrazzo': 14,
    'blueprint': 15, 'newsprint': 15, 'hologram': 15, 'arcade': 16,
    'jazz': 16, 'spaceport': 17, 'candy': 17, 'noir-film': 18,
    'custom': 8,
}

PERSONALIZATION_LEVELS = {
    'chat_bubble_style': {
        'default': 1, 'comic': 2, 'typewriter': 4, 'sticker': 5,
        'zen': 6, 'newspaper': 7, 'toybrick': 8, 'hero': 9,
        'bauhaus': 10, 'receipt': 11, 'dragon': 12, 'mosaic': 13,
        'niko': 16, 'fufu': 17, 'xiaobai': 18,
    },
    'avatar_frame_style': {
        'none': 1, 'orbit': 2, 'polaroid': 3, 'camera': 4,
        'soundwave': 5, 'butterfly': 6, 'aurora': 7, 'moon': 8,
        'papercut': 9, 'comet': 10, 'snowfall': 11, 'portal': 12,
        'mechanical': 13, 'niko-run': 16, 'fufu-wave': 17, 'xiaobai-run': 18,
    },
}

VIP_OR_LEVEL_PERSONALIZATION = {
    ('chat_bubble_style', 'niko'), ('chat_bubble_style', 'fufu'), ('chat_bubble_style', 'xiaobai'),
    ('avatar_frame_style', 'niko-run'), ('avatar_frame_style', 'fufu-wave'), ('avatar_frame_style', 'xiaobai-run'),
}


def _reward(reward_id, level, category, title, rarity, asset_key=None, capability_key=None, status='live'):
    preview_kind = 'live' if category in {'background', 'bubble', 'frame'} else 'image'
    destination = {
        'background': '/app/menu/personalization/background',
        'bubble': '/app/menu/personalization/bubble',
        'frame': '/app/menu/personalization/frame',
        'capability': '/app/menu/growth',
        'identity': '/app/menu/growth',
    }[category]
    return {
        'id': reward_id,
        'level': level,
        'category': category,
        'title': title,
        'title_key': f'growth.reward.{reward_id}.title',
        'description_key': f'growth.reward.{reward_id}.description',
        'rarity': rarity,
        'asset_key': asset_key,
        'capability_key': capability_key,
        'implementation_status': status,
        'preview_kind': preview_kind,
        'destination': destination,
        'vip_exclusive': False,
        'vip_access': 'level_or_vip' if (category, asset_key) in {
            ('bubble', 'niko'), ('bubble', 'fufu'), ('bubble', 'xiaobai'),
            ('frame', 'niko-run'), ('frame', 'fufu-wave'), ('frame', 'xiaobai-run'),
        } else None,
    }


LEVEL_REWARDS = {
    1: [_reward('capability.basic', 1, 'capability', '基础沟通', 'common', capability_key='basic'), _reward('background.default', 1, 'background', '默认背景', 'common', 'default'), _reward('bubble.default', 1, 'bubble', '默认气泡', 'common', 'default'), _reward('frame.none', 1, 'frame', '无边框', 'common', 'none')],
    2: [_reward('capability.image', 2, 'capability', '发送图片', 'rare', capability_key='send_image'), _reward('background.paper', 2, 'background', '纸张背景', 'common', 'paper'), _reward('bubble.comic', 2, 'bubble', '漫画气泡', 'uncommon', 'comic'), _reward('frame.orbit', 2, 'frame', '轨道头像框', 'uncommon', 'orbit')],
    3: [_reward('capability.audio', 3, 'capability', '发送语音', 'rare', capability_key='send_audio'), _reward('capability.location', 3, 'capability', '分享位置', 'uncommon', capability_key='send_location'), _reward('identity.level_tag', 3, 'identity', '等级签', 'uncommon'), _reward('background.mint', 3, 'background', '薄荷背景', 'common', 'mint'), _reward('frame.polaroid', 3, 'frame', '拍立得头像框', 'uncommon', 'polaroid')],
    4: [_reward('capability.group', 4, 'capability', '创建群聊并邀请好友', 'rare', capability_key='create_group'), _reward('capability.avatar', 4, 'capability', '自定义头像', 'rare', capability_key='custom_avatar'), _reward('background.dusk', 4, 'background', '黄昏背景', 'common', 'dusk'), _reward('bubble.typewriter', 4, 'bubble', '打字机气泡', 'uncommon', 'typewriter'), _reward('frame.camera', 4, 'frame', '相机头像框', 'uncommon', 'camera')],
    5: [_reward('capability.video', 5, 'capability', '发送视频', 'rare', capability_key='send_video'), _reward('capability.group_name', 5, 'capability', '修改群名称', 'uncommon', capability_key='rename_group'), _reward('capability.nickname_365', 5, 'capability', '昵称每 365 天可改', 'uncommon', capability_key='rename_nickname'), _reward('background.comic', 5, 'background', '漫画背景', 'uncommon', 'comic'), _reward('bubble.sticker', 5, 'bubble', '贴纸气泡', 'uncommon', 'sticker'), _reward('frame.soundwave', 5, 'frame', '声波头像框', 'rare', 'soundwave')],
    6: [_reward('capability.welcome', 6, 'capability', '自定义欢迎语与广场招呼', 'rare', capability_key='welcome_message'), _reward('background.zen', 6, 'background', '禅意背景', 'uncommon', 'zen'), _reward('bubble.zen', 6, 'bubble', '禅意气泡', 'uncommon', 'zen'), _reward('frame.butterfly', 6, 'frame', '蝴蝶头像框', 'legendary', 'butterfly')],
    7: [_reward('capability.online', 7, 'capability', '好友上线提醒', 'rare', capability_key='online_reminder'), _reward('background.hero', 7, 'background', '英雄背景', 'uncommon', 'hero'), _reward('bubble.newspaper', 7, 'bubble', '报纸气泡', 'uncommon', 'newspaper'), _reward('frame.aurora', 7, 'frame', '极光头像框', 'epic', 'aurora')],
    8: [_reward('capability.audio_download', 8, 'capability', '下载语音', 'uncommon', capability_key='download_audio'), _reward('capability.nickname_30', 8, 'capability', '昵称每 30 天可改', 'rare', capability_key='rename_nickname'), _reward('capability.custom_background', 8, 'capability', '上传自定义聊天背景', 'epic', capability_key='custom_chat_background'), _reward('background.dragon', 8, 'background', '游龙背景', 'uncommon', 'dragon'), _reward('background.bauhaus', 8, 'background', '包豪斯背景', 'uncommon', 'bauhaus'), _reward('bubble.toybrick', 8, 'bubble', '玩具积木气泡', 'rare', 'toybrick'), _reward('frame.moon', 8, 'frame', '月相头像框', 'rare', 'moon')],
    9: [_reward('background.mosaic', 9, 'background', '马赛克背景', 'uncommon', 'mosaic'), _reward('background.tidepool', 9, 'background', '潮池背景', 'rare', 'tidepool'), _reward('bubble.hero', 9, 'bubble', '英雄气泡', 'rare', 'hero'), _reward('frame.papercut', 9, 'frame', '剪纸头像框', 'rare', 'papercut')],
    10: [_reward('capability.notification', 10, 'capability', '自定义消息通知', 'epic', capability_key='custom_notification_message'), _reward('background.forest', 10, 'background', '森林背景', 'rare', 'forest'), _reward('background.desert', 10, 'background', '沙漠背景', 'rare', 'desert'), _reward('bubble.bauhaus', 10, 'bubble', '包豪斯气泡', 'rare', 'bauhaus'), _reward('frame.comet', 10, 'frame', '彗星头像框', 'legendary', 'comet')],
    11: [_reward('background.sunrise', 11, 'background', '日出背景', 'rare', 'sunrise'), _reward('background.snowfield', 11, 'background', '雪原背景', 'rare', 'snowfield'), _reward('bubble.receipt', 11, 'bubble', '小票气泡', 'rare', 'receipt'), _reward('frame.snowfall', 11, 'frame', '落雪头像框', 'epic', 'snowfall')],
    12: [_reward('capability.nickname_7', 12, 'capability', '昵称每 7 天可改', 'epic', capability_key='rename_nickname'), _reward('background.sakura', 12, 'background', '樱花背景', 'rare', 'sakura'), _reward('background.midnight', 12, 'background', '午夜背景', 'rare', 'midnight'), _reward('bubble.dragon', 12, 'bubble', '游龙气泡', 'epic', 'dragon'), _reward('frame.portal', 12, 'frame', '传送门头像框', 'epic', 'portal'), _reward('profile.card_theme', 12, 'identity', '个人名片主题系统', 'legendary', status='planned')],
    13: [_reward('background.rain', 13, 'background', '雨幕背景', 'rare', 'rain'), _reward('background.galaxy', 13, 'background', '星系背景', 'rare', 'galaxy'), _reward('bubble.mosaic', 13, 'bubble', '马赛克气泡', 'epic', 'mosaic'), _reward('frame.mechanical', 13, 'frame', '机械头像框', 'epic', 'mechanical')],
    14: [_reward('background.aurora_sky', 14, 'background', '极光天空背景', 'epic', 'aurora-sky'), _reward('background.linen', 14, 'background', '亚麻背景', 'rare', 'linen'), _reward('background.terrazzo', 14, 'background', '水磨石背景', 'rare', 'terrazzo')],
    15: [_reward('background.blueprint', 15, 'background', '蓝图背景', 'rare', 'blueprint'), _reward('background.newsprint', 15, 'background', '新闻纸背景', 'rare', 'newsprint'), _reward('background.hologram', 15, 'background', '全息背景', 'epic', 'hologram')],
    16: [_reward('background.arcade', 16, 'background', '街机背景', 'epic', 'arcade'), _reward('background.jazz', 16, 'background', '爵士背景', 'rare', 'jazz'), _reward('bubble.niko', 16, 'bubble', 'Niko 气泡', 'epic', 'niko'), _reward('frame.niko', 16, 'frame', 'Niko Run 头像框', 'legendary', 'niko-run'), _reward('growth.report', 16, 'identity', '成长报告', 'epic', status='planned')],
    17: [_reward('background.spaceport', 17, 'background', '太空港背景', 'epic', 'spaceport'), _reward('background.candy', 17, 'background', '糖果背景', 'rare', 'candy'), _reward('bubble.fufu', 17, 'bubble', 'Fufu 气泡', 'epic', 'fufu'), _reward('frame.fufu', 17, 'frame', 'Fufu Wave 头像框', 'legendary', 'fufu-wave'), _reward('frame.collection', 17, 'identity', '稀有头像框系列入口', 'epic', status='planned')],
    18: [_reward('background.noir', 18, 'background', '黑色电影背景', 'epic', 'noir-film'), _reward('bubble.xiaobai', 18, 'bubble', 'Xiaobai 气泡', 'epic', 'xiaobai'), _reward('frame.xiaobai', 18, 'frame', 'Xiaobai 迎接头像框', 'legendary', 'xiaobai-run'), _reward('identity.final_badge', 18, 'identity', '“尽兴”永久成长徽记', 'legendary', status='planned'), _reward('profile.final_slot', 18, 'identity', '满级个人名片展示位', 'legendary', status='planned')],
}


def level_unlock_titles(level):
    return [reward['title'] for reward in LEVEL_REWARDS.get(level, [])]
