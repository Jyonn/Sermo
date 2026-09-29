import threading
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from Message.preview_queue import platform_for_url, platform_slot, submit_preview


class PreviewQueueTests(SimpleTestCase):
    def test_each_music_service_has_its_own_queue(self):
        cases = {
            'https://v.douyin.com/example/': 'douyin',
            'https://xhslink.cn/o/example': 'xiaohongshu',
            'https://163cn.tv/example': 'netease',
            'https://y.qq.com/n/ryqq/songDetail/example': 'qq',
            'https://t1.kugou.com/example': 'kugou',
            'https://qishui.douyin.com/example': 'qishui',
            'https://music.apple.com/cn/album/example': 'apple',
            'https://www.kuwo.cn/play_detail/example': 'kuwo',
            'https://example.com/article': 'other',
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(platform_for_url(url), expected)

    def test_same_platform_is_sequential_while_other_platform_runs(self):
        first_started = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        other_started = threading.Event()

        def first():
            first_started.set()
            release.wait(2)

        try:
            first_future = submit_preview('https://v.douyin.com/a/', first)
            self.assertTrue(first_started.wait(2))
            second_future = submit_preview('https://www.douyin.com/video/1234567890123456789', second_started.set)
            other_future = submit_preview('https://xhslink.cn/o/a', other_started.set)
            self.assertTrue(other_started.wait(2))
            self.assertFalse(second_started.wait(0.1))
        finally:
            release.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)
        other_future.result(timeout=2)
        self.assertTrue(second_started.is_set())

    def test_manual_refresh_uses_the_same_platform_slot(self):
        entered = threading.Event()

        def enter_slot():
            with platform_slot('https://v.douyin.com/a/'):
                entered.set()

        with platform_slot('https://v.douyin.com/b/'):
            future = submit_preview('https://v.douyin.com/a/', enter_slot)
            self.assertFalse(entered.wait(0.1))
        future.result(timeout=2)
        self.assertTrue(entered.is_set())

    def test_mysql_slot_uses_named_lock_for_cross_process_serialization(self):
        cursor = Mock()
        cursor.fetchone.return_value = (1,)
        db = Mock(vendor='mysql')
        db.cursor.return_value.__enter__ = Mock(return_value=cursor)
        db.cursor.return_value.__exit__ = Mock(return_value=False)
        with patch('Message.preview_queue.connection', db):
            with platform_slot('https://v.douyin.com/a/'):
                pass
        self.assertEqual(cursor.execute.call_args_list[0].args[0], 'SELECT GET_LOCK(%s, %s)')
        self.assertEqual(cursor.execute.call_args_list[0].args[1], ['sermo:link-preview:douyin', -1])
        self.assertEqual(cursor.execute.call_args_list[1].args[0], 'SELECT RELEASE_LOCK(%s)')
