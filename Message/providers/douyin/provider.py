"""Platform identification; media resolution is exclusively handled by JustOneAPI."""
from urllib.parse import urlparse
from Message.providers.justone import JustOneProvider


class DouyinProvider:
    HOSTS = JustOneProvider.DY

    @classmethod
    def supports(cls, url):
        return urlparse(url or '').hostname in cls.HOSTS
