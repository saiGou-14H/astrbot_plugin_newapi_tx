"""Offline Codex request contract tests; every client uses MockTransport."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import threading
import traceback
import unittest
from unittest.mock import AsyncMock, patch

import httpx

if 'compat_plugin' not in sys.modules:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        'compat_plugin', root / '__init__.py', submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError('Cannot locate compat_plugin')
    sys.modules['compat_plugin'] = importlib.util.module_from_spec(spec)

from compat_plugin import newapi_utils
from compat_plugin.newapi_utils import CodexGenerationError, NewApiCore

KEY = 'PRIVATE_KEY_SENTINEL'
URL = 'http://10.0.0.2:8080/PRIVATE_URL_SENTINEL'
BODY = 'PRIVATE_BODY_SENTINEL'
PROMPT = 'PRIVATE_PROMPT_SENTINEL'
REAL_CLIENT = httpx.AsyncClient
REAL_WAIT_FOR = asyncio.wait_for


class CodexCompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = NewApiCore({'codex_settings': {'api_key': KEY, 'base_url': URL}})
        self.conf = self.core.config['codex_settings']
        self.requests = []
        self.response = httpx.Response(200, json={'choices': [{'message': {'content': '<svg/>'}}]})
        self.failure = None
        self.handler = None

        async def transport(request):
            self.requests.append(request)
            if self.failure is not None:
                raise self.failure
            if self.handler is not None:
                return await self.handler(request)
            return self.response

        def client_factory(**kwargs):
            return REAL_CLIENT(transport=httpx.MockTransport(transport), **kwargs)

        self.client_patch = patch.object(newapi_utils.httpx, 'AsyncClient', side_effect=client_factory)
        self.client_mock = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        # Fail closed if implementation creates another transport and tries real IO.
        guard = patch.object(httpx.AsyncHTTPTransport, 'handle_async_request',
                             new_callable=AsyncMock, side_effect=AssertionError('Real network forbidden'))
        self.network = guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.network.assert_not_awaited)
        logs = patch.object(newapi_utils, 'logger')
        self.logger = logs.start()
        self.addCleanup(logs.stop)

    async def assert_failure(self, code, status=None):
        with self.assertRaises(CodexGenerationError) as caught:
            await self.core.codex_chat_completion(PROMPT)
        exc = caught.exception
        self.assertEqual(exc.code, code)
        self.assertEqual(exc.http_status, status)
        self.assertIsNone(exc.__context__)
        self.assertIsNone(exc.__cause__)
        rendered = ''.join(traceback.format_exception(exc)) + repr(vars(exc)) + str(self.logger.mock_calls)
        for secret in (KEY, URL, BODY, PROMPT):
            self.assertNotIn(secret, rendered)
        self.assertLessEqual(len(self.requests), 1)
        self.logger.warning.assert_called_once()
        self.assertRegex(self.logger.warning.call_args.args[0],
                         r'^\[NewAPI Codex\] code=[A-Z_]+ http_status=(None|\d+) elapsed=\d+\.\d{3}$')
        return exc

    async def test_success_and_default_timeout(self):
        with patch.object(newapi_utils.asyncio, 'wait_for', wraps=REAL_WAIT_FOR) as deadline:
            result = await self.core.codex_chat_completion(PROMPT)
        self.assertEqual(result, '<svg/>')
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(str(request.url), URL + '/v1/chat/completions')
        self.assertEqual(request.headers['Authorization'], 'Bearer ' + KEY)
        self.assertEqual(json.loads(request.content), {
            'model': 'gpt-6-astra', 'messages': [{'role': 'user', 'content': PROMPT}], 'stream': False,
        })
        self.assertEqual(request.extensions['timeout'], {'read': 600, 'connect': 10, 'write': 30, 'pool': 30})
        deadline.assert_called_once()
        self.assertEqual(deadline.call_args.kwargs['timeout'], 600)

    async def test_configured_model_and_empty_model_fallback(self):
        for configured, expected in [('gpt-6.1-sol', 'gpt-6.1-sol'), ('', 'gpt-6-astra'), (None, 'gpt-6-astra')]:
            with self.subTest(model=configured):
                self.conf['model'] = configured
                await self.core.codex_chat_completion(PROMPT)
                self.assertEqual(json.loads(self.requests[-1].content)['model'], expected)

    async def test_finite_timeout_parameters(self):
        for timeout in (10, 180, 240.5, 599, 600, '600'):
            with self.subTest(timeout=timeout):
                self.conf['timeout_seconds'] = timeout
                with patch.object(newapi_utils.asyncio, 'wait_for', wraps=REAL_WAIT_FOR) as deadline:
                    await self.core.codex_chat_completion(PROMPT)
                self.assertEqual(deadline.call_args.kwargs['timeout'], float(timeout))
                self.assertEqual(self.requests[-1].extensions['timeout'], {
                    'read': float(timeout), 'connect': 10,
                    'write': min(30, float(timeout)), 'pool': min(30, float(timeout)),
                })

    async def test_invalid_timeout_configurations(self):
        for value in (None, True, False, 0, -1, 9.99, 600.01, float('nan'),
                      float('inf'), -float('inf'), 'nan', 'inf', '', 'oops', [], {}):
            with self.subTest(value=value):
                self.conf['timeout_seconds'] = value
                self.logger.reset_mock()
                await self.assert_failure('CONFIGURATION_ERROR')
                self.client_mock.assert_not_called()

    async def test_missing_and_invalid_configuration(self):
        for conf in ({}, {'api_key': KEY}, {'api_key': ' ', 'base_url': URL},
                     {'api_key': KEY, 'base_url': ' '}, {'api_key': KEY, 'base_url': 'bad'},
                     {'api_key': KEY, 'base_url': 'ftp://internal'},
                     {'api_key': 123, 'base_url': URL}, ['bad']):
            with self.subTest(conf=conf):
                self.core.config['codex_settings'] = conf
                self.logger.reset_mock()
                await self.assert_failure('CONFIGURATION_ERROR')
                self.client_mock.assert_not_called()

    async def test_fallback_credentials_and_endpoint(self):
        self.core.config['codex_settings'] = {}
        self.core.api_access_token = 'Bearer ' + KEY
        self.core.api_base_url = URL
        self.assertEqual(await self.core.codex_chat_completion(PROMPT), '<svg/>')
        self.assertEqual(self.requests[0].headers['Authorization'], 'Bearer ' + KEY)

    async def test_http_status_precedes_invalid_body(self):
        for status in (301, 400, 401, 429, 500, 503):
            with self.subTest(status=status):
                self.requests.clear()
                self.logger.reset_mock()
                self.response = httpx.Response(status, content=BODY)
                with patch.object(self.response, 'json', side_effect=AssertionError('Must not parse error body')):
                    await self.assert_failure('HTTP_ERROR', status)

    async def test_timeout_and_network_exception_classification(self):
        for kind, code in ((httpx.ReadTimeout, 'TIMEOUT'), (httpx.ConnectTimeout, 'TIMEOUT'),
                           (httpx.WriteTimeout, 'TIMEOUT'), (httpx.PoolTimeout, 'TIMEOUT'),
                           (httpx.ConnectError, 'NETWORK_ERROR'), (httpx.ReadError, 'NETWORK_ERROR'),
                           (httpx.RemoteProtocolError, 'NETWORK_ERROR')):
            with self.subTest(kind=kind):
                self.requests.clear()
                self.logger.reset_mock()
                self.failure = kind(KEY + URL + BODY)
                await self.assert_failure(code)

    async def test_http_status_exception_is_safe(self):
        request = httpx.Request('POST', URL)
        self.failure = httpx.HTTPStatusError(KEY + BODY, request=request,
                                           response=httpx.Response(502, request=request))
        await self.assert_failure('HTTP_ERROR', 502)

    async def test_invalid_json_and_response_shapes(self):
        for payload in (None, [], 'text', {}, {'choices': None}, {'choices': {}},
                        {'choices': [None]}, {'choices': ['text']}, {'choices': [{}]},
                        {'choices': [{'message': None}]}, {'choices': [{'message': []}]},
                        {'choices': [{'message': {}}]},
                        *({'choices': [{'message': {'content': value}}]} for value in (None, 3, [], {}, True))):
            with self.subTest(payload=payload):
                self.requests.clear()
                self.logger.reset_mock()
                self.response = httpx.Response(200, content=json.dumps(payload))
                await self.assert_failure('INVALID_RESPONSE')
        self.requests.clear()
        self.logger.reset_mock()
        self.response = httpx.Response(200, content=BODY)
        await self.assert_failure('INVALID_RESPONSE')

    async def test_empty_response_variants(self):
        for response in (httpx.Response(200, content=b''), httpx.Response(200, content=b'  '),
                         httpx.Response(200, json={'choices': []}),
                         httpx.Response(200, json={'choices': [{'message': {'content': ''}}]}),
                         httpx.Response(200, json={'choices': [{'message': {'content': ' \n '}}]})):
            with self.subTest(response=response):
                self.requests.clear()
                self.logger.reset_mock()
                self.response = response
                await self.assert_failure('EMPTY_RESPONSE')

    async def test_total_deadline_cancels_pending_request(self):
        cancelled = asyncio.Event()

        async def pending(request):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def fast_deadline(coro, timeout):
            self.assertEqual(timeout, 600)
            return await REAL_WAIT_FOR(coro, timeout=0.02)

        self.handler = pending
        with patch.object(newapi_utils.asyncio, 'wait_for', side_effect=fast_deadline):
            await self.assert_failure('TIMEOUT')
        self.assertTrue(cancelled.is_set())

    async def test_total_deadline_includes_json_parsing(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_json():
            entered.set()
            release.wait(2)
            return {'choices': [{'message': {'content': '<svg/>'}}]}

        async def fast_deadline(coro, timeout):
            self.assertEqual(timeout, 600)
            return await REAL_WAIT_FOR(coro, timeout=0.05)

        try:
            with patch.object(self.response, 'json', side_effect=slow_json), \
                    patch.object(newapi_utils.asyncio, 'wait_for', side_effect=fast_deadline):
                await self.assert_failure('TIMEOUT')
            self.assertTrue(entered.is_set())
        finally:
            release.set()

    async def test_external_cancellation_propagates(self):
        entered = asyncio.Event()

        async def pending(request):
            entered.set()
            await asyncio.Event().wait()

        self.handler = pending
        task = asyncio.create_task(self.core.codex_chat_completion(PROMPT))
        await REAL_WAIT_FOR(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.requests), 1)
        self.logger.warning.assert_not_called()

    def test_error_contract_has_only_safe_fields(self):
        self.assertTrue(issubclass(CodexGenerationError, RuntimeError))
        self.assertEqual(CodexGenerationError.CODES, {
            'TIMEOUT', 'HTTP_ERROR', 'NETWORK_ERROR', 'INVALID_RESPONSE',
            'EMPTY_RESPONSE', 'CONFIGURATION_ERROR',
        })
        for unsafe in (KEY, True, 99, 600, 500.0, {}, None):
            error = CodexGenerationError(KEY, unsafe)
            self.assertEqual(error.code, 'INVALID_RESPONSE')
            self.assertIsNone(error.http_status)
            self.assertNotIn(KEY, str(error))


if __name__ == '__main__':
    unittest.main()
