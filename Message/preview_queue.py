"""Bound concurrent link-preview parsing by source platform."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from urllib.parse import urlparse

from django.db import connection

from Message.providers.douyin import DouyinProvider
from Message.providers.music import MusicProvider
from Message.providers.xiaohongshu import XiaohongshuProvider


_EXECUTORS = {}
_EXECUTORS_LOCK = threading.Lock()
_PLATFORM_LOCKS = {}


def platform_for_url(url):
    host = (urlparse(url or '').hostname or '').lower()
    if DouyinProvider.supports(url):
        return 'douyin'
    if XiaohongshuProvider.supports(url):
        return 'xiaohongshu'
    if host in ('163cn.tv', 'music.163.com', 'y.music.163.com'):
        return 'netease'
    for name, hosts in (
        ('qq', MusicProvider.QQ_HOSTS),
        ('kugou', MusicProvider.KUGOU_HOSTS),
        ('qishui', MusicProvider.QISHUI_HOSTS),
        ('apple', MusicProvider.APPLE_HOSTS),
        ('kuwo', MusicProvider.KUWO_HOSTS),
    ):
        if host in hosts:
            return name
    return 'other'


def submit_preview(url, callback, *args):
    platform = platform_for_url(url)
    with _EXECUTORS_LOCK:
        if platform not in _EXECUTORS:
            _EXECUTORS[platform] = ThreadPoolExecutor(
                max_workers=4 if platform == 'other' else 1,
                thread_name_prefix=f'preview-{platform}',
            )
            if platform != 'other':
                _PLATFORM_LOCKS.setdefault(platform, threading.Lock())
        executor = _EXECUTORS[platform]
    return executor.submit(callback, *args)


@contextmanager
def platform_slot(url):
    platform = platform_for_url(url)
    if platform == 'other':
        yield
        return
    with _EXECUTORS_LOCK:
        lock = _PLATFORM_LOCKS.setdefault(platform, threading.Lock())
    with lock:
        if connection.vendor != 'mysql':
            yield
            return
        lock_name = f'sermo:link-preview:{platform}'
        with connection.cursor() as cursor:
            cursor.execute('SELECT GET_LOCK(%s, %s)', [lock_name, -1])
            if cursor.fetchone()[0] != 1:
                raise RuntimeError(f'preview queue unavailable: {platform}')
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT RELEASE_LOCK(%s)', [lock_name])
