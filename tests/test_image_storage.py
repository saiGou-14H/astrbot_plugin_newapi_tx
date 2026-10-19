"""Offline storage contract tests; SDK construction is real, all I/O is mocked.

Run: python -B -m unittest discover -s tests -p test_image_storage.py -v
Requires the optional MinIO SDK and httpx only for this test suite.
"""
import asyncio
import builtins
import importlib.util
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import traceback
import unittest
from unittest.mock import patch
import zlib

import httpx
import minio
import urllib3

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('storage_under_test', ROOT / 'image_storage.py')
storage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(storage)


def png_chunk(kind, payload):
    return (struct.pack('>I', len(payload)) + kind + payload
            + struct.pack('>I', zlib.crc32(kind + payload)))


HEADER = png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
DATA = png_chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00\xff'))
END = png_chunk(b'IEND', b'')
PNG = storage.PNG_MAGIC + HEADER + DATA + END
SECRET = 'PRIVATE_SECRET_SENTINEL'
ACCESS = 'PRIVATE_ACCESS_SENTINEL'
CONFIG = dict(enabled=True, endpoint='https://minio.example.test:9000',
              public_base_url='https://images.example.test/', bucket='astrbot-images',
              prefix='reports/tihudace', access_key=ACCESS, secret_key=SECRET)


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'private-user-and-group.png'
        self.path.write_bytes(PNG)
        self.requests = []
        self.responses = {}
        self.puts = []
        self.main_thread = threading.get_ident()
        # A regression must never touch real HTTP or S3, even if error masking
        # turns the guard exception into an ImageStorageError.
        for target in ('socket.socket.connect', 'socket.create_connection',
                       'urllib3.PoolManager.urlopen'):
            guard = patch(target, side_effect=AssertionError('Real network forbidden'))
            mocked = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(mocked.assert_not_called)
        put = patch.object(minio.Minio, 'put_object', side_effect=self.record_put)
        self.put = put.start()
        self.addCleanup(put.stop)
        self.real_client = httpx.AsyncClient
        client = patch.object(httpx, 'AsyncClient', side_effect=self.client)
        self.client_factory = client.start()
        self.addCleanup(client.stop)

    def record_put(self, bucket, object_name, stream, length, **kwargs):
        self.puts.append((bucket, object_name, stream.read(), length, kwargs, threading.get_ident()))

    def client(self, **kwargs):
        return self.real_client(transport=httpx.MockTransport(self.respond), **kwargs)

    def respond(self, request):
        self.requests.append(request)
        result = self.responses.get(request.method)
        if isinstance(result, Exception):
            raise result
        if result is not None:
            status, headers, body = result
        else:
            status, headers, body = 200, {'Content-Type': 'image/png', 'Content-Length': str(len(PNG))}, PNG
        return httpx.Response(status, headers=headers,
                              stream=httpx.ByteStream(body if request.method == 'GET' else b''))

    async def assert_code(self, code, config=None, path=None):
        with self.assertRaises(storage.ImageStorageError) as caught:
            await storage.upload_tihudace_png(self.path if path is None else path,
                                             CONFIG if config is None else config)
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertEqual(str(error), code)
        rendered = ''.join(traceback.format_exception(error))
        for secret in (SECRET, ACCESS, 'REMOTE_PRIVATE_SENTINEL'):
            self.assertNotIn(secret, rendered)
        return error

    async def test_success_real_sdk_constructor_anonymous_head_get_random_prefix(self):
        urls = [await storage.upload_tihudace_png(self.path, CONFIG) for _ in range(2)]
        self.assertNotEqual(*urls)
        for url, put in zip(urls, self.puts):
            bucket, name, data, length, kwargs, thread_id = put
            self.assertEqual(bucket, 'astrbot-images')
            self.assertRegex(name, r'^reports/tihudace/\d{4}/\d{2}/\d{2}/[0-9a-f]{32}\.png$')
            self.assertEqual(url, 'https://images.example.test/astrbot-images/' + name)
            self.assertEqual(data, PNG)
            self.assertEqual(length, len(PNG))
            self.assertEqual(kwargs, {'content_type': 'image/png'})
            self.assertNotEqual(thread_id, self.main_thread)
            for secret in (SECRET, ACCESS, self.path.name):
                self.assertNotIn(secret, url)
        self.assertEqual([r.method for r in self.requests], ['HEAD', 'GET', 'HEAD', 'GET'])
        for request in self.requests:
            self.assertNotIn('authorization', request.headers)
            self.assertNotIn('cookie', request.headers)
            self.assertFalse(request.url.query)
        options = self.client_factory.call_args.kwargs
        self.assertFalse(options['trust_env'])
        self.assertFalse(options['follow_redirects'])

    async def test_disabled_never_reads_or_imports_dependencies(self):
        original_import = builtins.__import__
        def no_optional(name, *args, **kwargs):
            if name in ('minio', 'urllib3', 'httpx'):
                raise AssertionError('Optional dependency imported when disabled')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=no_optional):
            fresh = importlib.util.module_from_spec(SPEC)
            SPEC.loader.exec_module(fresh)
            with self.assertRaises(fresh.ImageStorageError) as caught:
                await fresh.upload_tihudace_png('/does-not-exist', {'enabled': False})
            self.assertEqual(caught.exception.code, 'disabled')
        self.put.assert_not_called()
        self.client_factory.assert_not_called()

    async def test_missing_dependencies_have_stable_code(self):
        for dependency in ('minio', 'httpx', 'urllib3'):
            with self.subTest(dependency=dependency), patch.dict(sys.modules, {dependency: None}):
                await self.assert_code('dependency_missing')
        self.put.assert_not_called()
        self.client_factory.assert_not_called()

    async def test_invalid_configuration(self):
        invalid = {
            'enabled': ['true', 1, None],
            'endpoint': ['not-a-url', 'https://user:password@host', 'https://host/path',
                         'https://host:bad', 'https://host:0', 'https://host:65536', 'https://[broken'],
            'public_base_url': ['http://host', 'https://host/path', 'https://host/a)[attack](https://evil)',
                                'https://host)', 'https://host?token=x', 'https://host?', 'https://host#',
                                'https://user:password@host', 'https://ho\nst', ' https://host',
                                'https://host/\t', 'https://host\\evil', 'https://host/%29',
                                'https://host//', 'https://host:abc', 'https://host:', 'https://bad_host',
                                'https://[::1]extra', 'https://[::1]:443:443'],
            'bucket': ['', 'UPPER', 'ab', 'a..b', 'a.-b', '127.0.0.1', 'a/b', None],
            'prefix': ['', '/', '/reports', 'reports/', 'reports//private', '../reports',
                       'reports/..', 'reports/用户', 'reports/x)', 'x' * 257, None],
            'access_key': ['', ' ', None], 'secret_key': ['', None, 123],
        }
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    await self.assert_code('invalid_configuration', dict(CONFIG, **{key: value}))
        await self.assert_code('invalid_configuration', config=[])
        self.put.assert_not_called()
        self.client_factory.assert_not_called()

    async def test_root_ipv6_and_http_endpoint_are_accepted(self):
        config = dict(CONFIG, endpoint='http://[::1]:9000/', public_base_url='https://[2001:db8::1]:443/')
        url = await storage.upload_tihudace_png(self.path, config)
        self.assertTrue(url.startswith('https://[2001:db8::1]:443/astrbot-images/'))

    async def test_png_structure_and_crc_rejected(self):
        bad_header = png_chunk(b'IHDR', struct.pack('>IIBBBBB', 0, 1, 8, 6, 0, 0, 0))
        samples = [b'', b'<svg/>', storage.PNG_MAGIC + b'0' * 20,
                   PNG[:-1], PNG + b'extra', PNG[:40] + bytes([PNG[40] ^ 1]) + PNG[41:],
                   storage.PNG_MAGIC + HEADER + END,
                   storage.PNG_MAGIC + bad_header + DATA + END,
                   storage.PNG_MAGIC + HEADER + HEADER + DATA + END,
                   storage.PNG_MAGIC + HEADER + png_chunk(b'ABCD', b'') + DATA + END]
        for data in samples:
            with self.subTest(size=len(data)):
                self.path.write_bytes(data)
                await self.assert_code('invalid_png')
        self.put.assert_not_called()

    async def test_png_size_limit_inclusive_and_bounded_read(self):
        with patch.object(storage, 'MAX_PNG_BYTES', len(PNG)):
            await storage.upload_tihudace_png(self.path, CONFIG)
            self.path.write_bytes(PNG + b'\x00')
            await self.assert_code('png_too_large')
        self.assertEqual(len(self.puts), 1)

    async def test_unreadable_file_sanitized(self):
        await self.assert_code('png_unreadable', path=self.path.parent / 'absent')
        await self.assert_code('png_unreadable', path=self.path.parent)
        await self.assert_code('png_unreadable', path='bad\x00path')
        self.put.assert_not_called()

    async def test_upload_failure_is_sanitized_and_does_not_probe_public_url(self):
        self.put.side_effect = RuntimeError(SECRET + ACCESS + 'REMOTE_PRIVATE_SENTINEL')
        with patch.object(urllib3.PoolManager, 'clear') as clear:
            await self.assert_code('upload_failed')
        self.assertGreaterEqual(clear.call_count, 1)
        self.client_factory.assert_not_called()

    async def test_real_s3_storage_full_and_unknown_codes_are_sanitized(self):
        from minio.error import S3Error
        for sdk_code, expected in [('XMinioStorageFull', 'storage_full'),
                                   ('AccessDenied', 'upload_failed'),
                                   (SECRET, 'upload_failed')]:
            with self.subTest(expected=expected):
                self.put.reset_mock()
                self.put.side_effect = S3Error(
                    code=sdk_code, message=SECRET + ACCESS,
                    resource='https://private.test/?token=' + SECRET,
                    request_id='REMOTE_PRIVATE_SENTINEL', host_id=SECRET,
                    response=urllib3.response.HTTPResponse(status=507))
                with patch.object(urllib3.PoolManager, 'clear') as clear:
                    await self.assert_code(expected)
                self.put.assert_called_once()
                self.assertGreaterEqual(clear.call_count, 1)
                self.client_factory.assert_not_called()

    async def test_non_s3_error_cannot_spoof_storage_full(self):
        error = RuntimeError('XMinioStorageFull ' + SECRET)
        error.code = 'XMinioStorageFull'
        self.put.side_effect = error
        await self.assert_code('upload_failed')
        self.put.assert_called_once()
        self.client_factory.assert_not_called()

    def test_safe_error_code_rejects_unknown_and_non_string_values(self):
        for code in storage.SAFE_ERROR_CODES:
            self.assertEqual(storage.safe_error_code(storage.ImageStorageError(code)), code)
        for error in (RuntimeError(SECRET), storage.ImageStorageError(SECRET),
                      storage.ImageStorageError(['storage_full'])):
            self.assertEqual(storage.safe_error_code(error), 'upload_failed')

    async def test_sdk_constructor_failure_is_sanitized(self):
        with patch.object(minio, 'Minio', side_effect=ValueError(SECRET)):
            await self.assert_code('upload_failed')
        self.client_factory.assert_not_called()

    async def test_head_and_get_failures(self):
        variants = [
            ((403, {'Content-Type': 'image/png'}, b''), 'public_image_unavailable'),
            ((404, {}, b''), 'public_image_unavailable'),
            ((302, {'Location': 'https://other.test/'}, b''), 'public_image_unavailable'),
            ((200, {'Content-Type': 'text/html'}, PNG), 'public_image_unavailable'),
            ((200, {'Content-Type': 'image/png', 'Content-Length': 'bad'}, PNG), 'public_image_size_mismatch'),
            ((200, {'Content-Type': 'image/png', 'Content-Length': str(len(PNG)+1)}, PNG), 'public_image_size_mismatch'),
            (httpx.ReadTimeout(SECRET + 'REMOTE_PRIVATE_SENTINEL'), 'public_image_unavailable'),
        ]
        for method in ('HEAD', 'GET'):
            for response, code in variants:
                with self.subTest(method=method, response=type(response).__name__):
                    self.requests.clear()
                    self.responses = {method: response}
                    await self.assert_code(code)
                    self.assertEqual([r.method for r in self.requests],
                                     ['HEAD'] if method == 'HEAD' else ['HEAD', 'GET'])

    async def test_get_body_must_match_uploaded_png_without_length_header(self):
        for body, code in [(PNG[:-1], 'public_image_size_mismatch'),
                           (PNG + b'extra', 'public_image_size_mismatch'),
                           (b'x' * len(PNG), 'public_image_unavailable')]:
            with self.subTest(code=code, size=len(body)):
                self.responses = {'GET': (200, {'Content-Type': 'image/png'}, body)}
                await self.assert_code(code)
        self.responses = {'GET': (200, {'Content-Type': 'Image/PNG; charset=binary'}, PNG)}
        await storage.upload_tihudace_png(self.path, CONFIG)

    async def test_get_stream_chunks_and_midstream_error(self):
        class Stream(httpx.AsyncByteStream):
            def __init__(self, fail):
                self.fail = fail

            async def __aiter__(self):
                yield PNG[:10]
                if self.fail:
                    raise httpx.ReadError(SECRET + 'REMOTE_PRIVATE_SENTINEL')
                yield PNG[10:]

        original = self.respond
        for fail in (False, True):
            def respond(request):
                if request.method == 'HEAD':
                    return original(request)
                self.requests.append(request)
                return httpx.Response(200, headers={'Content-Type': 'image/png'}, stream=Stream(fail))
            with self.subTest(fail=fail), patch.object(self, 'respond', side_effect=respond):
                if fail:
                    await self.assert_code('public_image_unavailable')
                else:
                    await storage.upload_tihudace_png(self.path, CONFIG)

    async def test_head_cookies_not_used_for_get(self):
        self.responses = {'HEAD': (200, {'Content-Type': 'image/png', 'Set-Cookie': 'session=private'}, b'')}
        await storage.upload_tihudace_png(self.path, CONFIG)
        self.assertNotIn('cookie', self.requests[-1].headers)

    async def test_file_read_and_upload_do_not_block_event_loop(self):
        for operation in ('_read_png', '_put_png'):
            with self.subTest(operation=operation):
                entered = threading.Event()
                release = threading.Event()
                original = getattr(storage, operation)
                def slow(*args):
                    self.assertNotEqual(threading.get_ident(), self.main_thread)
                    entered.set()
                    if not release.wait(2):
                        raise AssertionError('Event loop did not progress during blocking operation')
                    return original(*args)
                with patch.object(storage, operation, side_effect=slow):
                    task = asyncio.create_task(storage.upload_tihudace_png(self.path, CONFIG))
                    try:
                        async with asyncio.timeout(1):
                            while not entered.is_set():
                                await asyncio.sleep(0.001)
                        self.assertFalse(task.done())
                    finally:
                        release.set()
                        await task

    async def test_cancellation_propagates(self):
        with patch.object(storage, '_verify_public', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await storage.upload_tihudace_png(self.path, CONFIG)


if __name__ == '__main__':
    unittest.main()
