"""Consumer-visible update decisions and invalid release responses."""
from contextlib import nullcontext
import unittest
from unittest.mock import Mock, patch

import requests

from downloader.utils.update_check import (
    LATEST_RELEASE_URL, check_for_update, update_failure_message)


class UpdateCheckTests(unittest.TestCase):
    def response(self, tag, **extra):
        response = Mock(status_code=200)
        response.json.return_value = {
            'tag_name': tag, 'body': '修复与改进', **extra}
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_version_comparison_is_numeric_and_never_downgrades(self):
        for tag, newer in [('v0.9.9', False), ('v0.14.1', False),
                           ('v0.14.10', True), ('v0.15.0', True)]:
            with self.subTest(tag=tag), patch(
                    'downloader.utils.update_check.APP_VERSION', '0.14.1'), patch(
                    'downloader.utils.update_check.requests.get',
                    return_value=self.response(tag)):
                result = check_for_update()
            self.assertEqual(result.newer, newer)
            self.assertEqual(result.notes, '修复与改进')
            self.assertEqual(
                result.url,
                'https://github.com/dwdw530/downLoad/releases/tag/' + tag)

    def test_bad_or_unstable_release_cannot_be_reported_as_current(self):
        for tag, extra in [('v0.15.0-beta', {}), ('v0.15.0', {'draft': True}),
                           ('v0.15.0', {'prerelease': True}), (None, {})]:
            with self.subTest(tag=tag, extra=extra), patch(
                    'downloader.utils.update_check.requests.get',
                    return_value=self.response(tag, **extra)):
                with self.assertRaises(ValueError):
                    check_for_update()

    def test_network_failure_is_not_a_latest_version_result(self):
        with patch('downloader.utils.update_check.requests.get',
                   side_effect=requests.Timeout):
            with self.assertRaises(requests.Timeout):
                check_for_update()

    def test_rate_limit_does_not_claim_proxy_or_network_is_broken(self):
        response = requests.Response()
        response.status_code = 403
        response.url = LATEST_RELEASE_URL
        response.headers['X-RateLimit-Remaining'] = '0'
        with patch('downloader.utils.update_check.requests.get',
                   return_value=nullcontext(response)):
            with self.assertRaises(requests.HTTPError) as caught:
                check_for_update()
        message = update_failure_message(caught.exception)
        self.assertNotEqual(
            message, update_failure_message(requests.ConnectionError()))

    def test_http_error_keeps_status_without_claiming_disconnection(self):
        response = requests.Response()
        response.status_code = 404
        response.url = LATEST_RELEASE_URL
        message = update_failure_message(requests.HTTPError(response=response))
        self.assertIn('HTTP 404', message)
        self.assertNotIn('检查网络', message)
