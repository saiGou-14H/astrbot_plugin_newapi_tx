"""Pure signing tests: synthetic secrets only, no HTTP or plugin initialization."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

spec = importlib.util.spec_from_file_location('_test_dingtalk_signing',
    Path(__file__).resolve().parents[1] / 'dingtalk_signing.py')
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)
WEBHOOK = 'https://oapi.dingtalk.com/robot/send?access_token=synthetic-token'
SECRET = 'SEC-test-signing-only'


class DingTalkSigningTests(unittest.TestCase):
    def test_known_signature_vector_and_single_url_encoding(self):
        # Independent fixture generated with OpenSSL HMAC-SHA256 + base64.
        url = signing.signed_webhook_url(WEBHOOK, SECRET, timestamp_ms=1600000000123)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['timestamp'], ['1600000000123'])
        self.assertEqual(query['sign'], ['yN/EMb8xl0s8mZUj5Czzp6WaLGibfvn7EK/RdBUjp2s='])
        self.assertEqual(query['access_token'], ['synthetic-token'])
        self.assertNotIn('%252F', url)
        self.assertNotIn(SECRET, url)

    def test_stale_signature_is_replaced_not_duplicated(self):
        url = signing.signed_webhook_url(WEBHOOK + '&timestamp=1&sign=old&sign=older',
                                         SECRET, timestamp_ms=1600000000123)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(len(query['sign']), 1)
        self.assertEqual(query['timestamp'], ['1600000000123'])

    def test_each_send_uses_current_millisecond_time(self):
        with patch.object(signing.time, 'time_ns', side_effect=[1600000000123000000, 1600000000124000000]):
            first = parse_qs(urlsplit(signing.signed_webhook_url(WEBHOOK, SECRET)).query)
            second = parse_qs(urlsplit(signing.signed_webhook_url(WEBHOOK, SECRET)).query)
        self.assertEqual(first['timestamp'], ['1600000000123'])
        self.assertEqual(second['timestamp'], ['1600000000124'])
        self.assertNotEqual(first['sign'], second['sign'])

    def test_unsigned_robots_are_preserved(self):
        self.assertEqual(signing.signed_webhook_url(WEBHOOK, ''), WEBHOOK)

    def test_invalid_endpoint_and_missing_access_token_are_rejected(self):
        for url in ('http://oapi.dingtalk.com/robot/send?access_token=x',
                    'https://example.invalid/robot/send?access_token=x',
                    'https://oapi.dingtalk.com/robot/send'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                signing.signed_webhook_url(url, SECRET)


if __name__ == '__main__':
    unittest.main()
