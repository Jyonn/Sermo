"""Platform identification; no scraping or legacy parsing service."""
from urllib.parse import urlparse
from Message.providers.justone import JustOneProvider


class XiaohongshuProvider:
    HOSTS = JustOneProvider.XHS

    @classmethod
    def supports(cls, url):
        return urlparse(url or '').hostname in cls.HOSTS
