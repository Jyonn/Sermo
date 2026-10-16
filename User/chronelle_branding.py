import base64
from functools import lru_cache
from pathlib import Path

APPLICATION_NAME = 'Frienden 友间'
AUTHORIZATION_PURPOSES = {
    'zh-cn': '把你的公开日程展示在个人名片中',
    'zh-tw': '在個人名片中展示你的公開行程',
    'en': 'Show your public schedule on your profile card',
    'ja': '公開スケジュールをプロフィールに表示',
    'ko': '공개 일정을 프로필 카드에 표시',
    'es': 'Mostrar tu agenda pública en tu tarjeta de perfil',
}


def authorization_purpose(language):
    return AUTHORIZATION_PURPOSES.get((language or '').replace('_', '-').lower(), AUTHORIZATION_PURPOSES['en'])


@lru_cache(maxsize=1)
def application_icon():
    image = Path(__file__).with_name('assets').joinpath('frienden-180.png').read_bytes()
    return 'data:image/png;base64,' + base64.b64encode(image).decode('ascii')
