import json
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from utils.content_safety import (
    ContentSafetyErrors,
    ContentSafetyScene,
    check_text,
    is_miniprogram_request,
)
from utils.middleware import APIPacker


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

        debug = check_text('正常内容', 'openid', ContentSafetyScene.COMMENT)

        self.assertEqual(perform_check.call_args.args[0]['version'], 2)
        self.assertEqual(perform_check.call_args.args[0]['openid'], 'openid')
        self.assertEqual(debug, {
            'scene': ContentSafetyScene.COMMENT,
            'errcode': 0,
            'errmsg': None,
            'trace_id': 'trace-pass',
            'result': {'suggest': 'pass', 'label': 100},
            'detail': None,
        })

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
                with self.assertRaises(type(ContentSafetyErrors.REJECTED)) as caught:
                    check_text('待审核内容', 'openid', ContentSafetyScene.SOCIAL)
                self.assertEqual(caught.exception.wechat_content_safety['trace_id'], f'trace-{suggestion}')
                self.assertEqual(caught.exception.wechat_content_safety['result']['suggest'], suggestion)

    @patch('utils.content_safety._access_token', return_value='token')
    @patch('utils.content_safety._perform_check')
    def test_provider_error_is_not_silently_allowed(self, perform_check, _access_token):
        perform_check.return_value = {'errcode': 61010, 'errmsg': 'code is expired'}

        with self.assertRaises(type(ContentSafetyErrors.UNAVAILABLE)) as caught:
            check_text('内容', 'openid', ContentSafetyScene.PROFILE)
        self.assertEqual(caught.exception.wechat_content_safety['errcode'], 61010)

    def test_api_response_exposes_sanitized_review_debug(self):
        debug = {
            'scene': ContentSafetyScene.COMMENT,
            'errcode': 0,
            'errmsg': 'ok',
            'trace_id': 'trace-debug',
            'result': {'suggest': 'pass', 'label': 100},
            'detail': [],
        }
        response = APIPacker.pack({'message_id': 1}, debug)
        payload = json.loads(response.content)

        self.assertEqual(payload['body'], {'message_id': 1})
        self.assertEqual(payload['wechat_content_safety'], debug)
        self.assertNotIn('openid', payload['wechat_content_safety'])
        self.assertNotIn('access_token', payload['wechat_content_safety'])
