from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from utils.content_safety import (
    ContentSafetyErrors,
    ContentSafetyScene,
    check_text,
    is_miniprogram_request,
)


class ContentSafetyTests(SimpleTestCase):
    def tearDown(self):
        cache.clear()

    def test_request_source_must_be_explicit_miniprogram(self):
        request = Mock(headers={'X-Sermo-Client': 'wechat-miniprogram'})
        self.assertTrue(is_miniprogram_request(request))
        request.headers = {}
        self.assertFalse(is_miniprogram_request(request))

    @patch('utils.content_safety._access_token', return_value='token')
    @patch('utils.content_safety._perform_check')
    def test_pass_result_is_accepted(self, perform_check, _access_token):
        perform_check.return_value = {
            'errcode': 0,
            'result': {'suggest': 'pass', 'label': 100},
            'trace_id': 'trace-pass',
        }

        check_text('正常内容', 'openid', ContentSafetyScene.COMMENT)

        self.assertEqual(perform_check.call_args.args[0]['version'], 2)
        self.assertEqual(perform_check.call_args.args[0]['openid'], 'openid')

    @patch('utils.content_safety._access_token', return_value='token')
    @patch('utils.content_safety._perform_check')
    def test_review_and_risky_results_are_rejected(self, perform_check, _access_token):
        for suggestion in ('review', 'risky'):
            with self.subTest(suggestion=suggestion):
                perform_check.return_value = {
                    'errcode': 0,
                    'result': {'suggest': suggestion, 'label': 20001},
                    'trace_id': f'trace-{suggestion}',
                }
                with self.assertRaises(type(ContentSafetyErrors.REJECTED)):
                    check_text('待审核内容', 'openid', ContentSafetyScene.SOCIAL)

    @patch('utils.content_safety._access_token', return_value='token')
    @patch('utils.content_safety._perform_check')
    def test_provider_error_is_not_silently_allowed(self, perform_check, _access_token):
        perform_check.return_value = {'errcode': 61010, 'errmsg': 'code is expired'}

        with self.assertRaises(type(ContentSafetyErrors.UNAVAILABLE)):
            check_text('内容', 'openid', ContentSafetyScene.PROFILE)
