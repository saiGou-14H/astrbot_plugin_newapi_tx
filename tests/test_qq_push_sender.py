"""Network-free sender tests with the inspected botpy API call signatures.

The SDK post_group_message signature below mirrors botpy/api.py in astrbot-6666.
No SDK clients or AstrBot runtime are initialized; every external call is mocked.
"""
import ast
import asyncio
import base64
import importlib.util
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, create_autospec, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qq_push_sender_under_test", ROOT / "qq_push_sender.py")
sender = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sender)


class SDKAPI:
    async def post_group_message(
        self, group_openid: str, msg_type: int = 0, content: str = None,
        embed=None, ark=None, message_reference=None, media=None, msg_id: str = None,
        msg_seq: int = 1, event_id: str = None, markdown=None, keyboard=None,
    ):
        raise AssertionError("SDK must be mocked")


class Route:
    def __init__(self, method, path, **parameters):
        self.method, self.path, self.parameters = method, path, parameters


# Prefer the installed SDK. A source-only checkout can point at the exact
# container SDK file copied for audit; execute its method body, never a rewrite.
try:
    from botpy.api import BotAPI as RealBotAPI
except ImportError:
    RealBotAPI = None


def real_sdk_post_group_message():
    if RealBotAPI is not None:
        return RealBotAPI.post_group_message
    source = os.environ.get("BOTPY_API_SOURCE")
    if not source:
        raise unittest.SkipTest("Install botpy or set BOTPY_API_SOURCE to its actual api.py")
    tree = ast.parse(Path(source).read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "BotAPI")
    method = next(node for node in cls.body
                  if isinstance(node, ast.AsyncFunctionDef)
                  and node.name == "post_group_message")
    # Postponed annotations avoid importing SDK TypedDict dependencies; the
    # method implementation and signature are left completely unchanged.
    extracted = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        method,
    ], type_ignores=[])
    namespace = {"Route": Route}
    exec(compile(ast.fix_missing_locations(extracted), source, "exec"), namespace)
    return namespace["post_group_message"]


class Chain:
    def __init__(self):
        self.text = self.image = None

    def message(self, text):
        self.text = text
        return self

    def file_image(self, path):
        self.image = path
        return self


class Session:
    def __init__(self, platform_name, message_type, session_id):
        self.platform_id = platform_name
        self.message_type = message_type
        self.session_id = session_id


def module(name, **attributes):
    value = ModuleType(name)
    value.__dict__.update(attributes)
    return value


class QQPushSenderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.png = str(Path(self.tmp.name) / "sample.png")
        self.image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
        Path(self.png).write_bytes(self.image)
        self.api = create_autospec(SDKAPI, instance=True)
        self.api._http = SimpleNamespace(request=AsyncMock(return_value={
            "file_uuid": "mock-uuid", "file_info": "mock-file-info", "ttl": 60}))
        self.api.post_group_message.return_value = {"id": "mock-message-id"}
        self.platform = SimpleNamespace(
            meta=lambda: SimpleNamespace(id="official-instance", name="qq_official"),
            client=SimpleNamespace(api=self.api),
            _session_scene={}, _session_last_message_id={},
        )
        self.platforms = {"official-instance": self.platform}
        self.context = SimpleNamespace(
            get_platform_inst=Mock(side_effect=self.platforms.get),
            send_message=AsyncMock(return_value=True),
        )
        self.route = {"platform_id": "official-instance", "platform_type": "qq_official",
                      "group_id": "SYNTHETIC_GROUP_OPENID"}
        self.group_type = object()
        stubs = {
            "botpy": module("botpy"), "botpy.http": module("botpy.http", Route=Route),
            "astrbot": module("astrbot"), "astrbot.api": module("astrbot.api"),
            "astrbot.api.event": module("astrbot.api.event", MessageChain=Chain),
            "astrbot.core": module("astrbot.core"),
            "astrbot.core.platform": module("astrbot.core.platform"),
            "astrbot.core.platform.message_session": module(
                "astrbot.core.platform.message_session", MessageSession=Session),
            "astrbot.core.platform.message_type": module(
                "astrbot.core.platform.message_type",
                MessageType=SimpleNamespace(GROUP_MESSAGE=self.group_type)),
        }
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, stubs).start()
        # Even a regression that tries opening a network connection must fail.
        patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")).start()
        patch.object(socket.socket, "connect_ex", side_effect=AssertionError("Network forbidden")).start()

    async def send(self, route=None):
        return await sender.send_qq_group_image(
            self.context, self.route if route is None else route, "summary", self.png)

    async def test_restart_empty_caches_uploads_and_sends_without_msg_id(self):
        self.assertTrue(await self.send())
        self.context.send_message.assert_not_awaited()
        self.assertEqual(self.platform._session_scene, {})
        self.assertEqual(self.platform._session_last_message_id, {})
        upload = self.api._http.request.call_args
        self.assertEqual(upload.args[0].path, "/v2/groups/{group_openid}/files")
        self.assertEqual(upload.args[0].parameters, {"group_openid": "SYNTHETIC_GROUP_OPENID"})
        self.assertEqual(upload.kwargs["json"], {
            "group_openid": "SYNTHETIC_GROUP_OPENID", "file_type": 1,
            "file_data": base64.b64encode(self.image).decode("ascii"), "srv_send_msg": False})
        payload = self.api.post_group_message.call_args.kwargs
        self.assertEqual(payload["group_openid"], "SYNTHETIC_GROUP_OPENID")
        self.assertEqual(payload["msg_type"], 7)
        self.assertEqual(payload["content"], "summary")
        self.assertEqual(payload["media"], {"file_info": "mock-file-info", "file_uuid": "mock-uuid"})
        self.assertTrue(1 <= payload["msg_seq"] <= 10000)
        self.assertNotIn("msg_id", payload)
        self.assertNotIn("event_id", payload)

    async def test_real_sdk_method_transmits_media_dict_to_http(self):
        method = real_sdk_post_group_message()
        http = SimpleNamespace(request=AsyncMock(side_effect=[
            {"file_uuid": "real-method-uuid", "file_info": "real-method-info"},
            {"id": "real-method-receipt"},
        ]))
        api = SimpleNamespace(_http=http)
        api.post_group_message = method.__get__(api, type(api))
        self.platform.client.api = api
        self.assertTrue(await self.send())
        self.assertEqual(http.request.await_count, 2)
        payload = http.request.call_args_list[1].kwargs["json"]
        self.assertIsInstance(payload["media"], dict)
        self.assertEqual(payload["media"], {
            "file_uuid": "real-method-uuid", "file_info": "real-method-info"})
        self.assertEqual(payload["group_openid"], "SYNTHETIC_GROUP_OPENID")
        self.assertEqual(payload["msg_type"], 7)
        self.assertEqual(payload["content"], "summary")
        # SDK locals() includes default msg_id=None despite our caller omitting
        # it; this is not a cached/passive-reply message ID.
        self.assertIsNone(payload["msg_id"])
        self.assertNotIn("self", payload)
        self.assertEqual(self.platform._session_scene, {})

    async def test_bad_message_receipts_fail(self):
        for receipt in (None, {}, {"id": ""}, {"id": "  "}, {"id": 123}, {"error": "private"}, True):
            with self.subTest(receipt=receipt):
                self.api.post_group_message.return_value = receipt
                self.assertFalse(await self.send())

    async def test_bad_upload_receipts_never_send(self):
        for receipt in (None, {}, {"file_info": "x"}, {"file_info": "", "file_uuid": "x"}, []):
            with self.subTest(receipt=receipt):
                self.api._http.request.return_value = receipt
                self.assertFalse(await self.send())
        self.api.post_group_message.assert_not_awaited()

    async def test_upload_exception_is_redacted(self):
        self.api._http.request.side_effect = RuntimeError("TOKEN_PRIVATE_SENTINEL")
        with self.assertLogs(sender.logger, level="WARNING") as logs:
            self.assertFalse(await self.send())
        self.assertNotIn("TOKEN_PRIVATE_SENTINEL", " ".join(logs.output))
        self.api.post_group_message.assert_not_awaited()

    async def test_message_exception_is_redacted(self):
        self.api.post_group_message.side_effect = RuntimeError("TOKEN_PRIVATE_SENTINEL")
        with self.assertLogs(sender.logger, level="WARNING") as logs:
            self.assertFalse(await self.send())
        self.assertNotIn("TOKEN_PRIVATE_SENTINEL", " ".join(logs.output))

    async def test_missing_platform(self):
        self.platforms.clear()
        self.assertFalse(await self.send())
        self.api._http.request.assert_not_awaited()

    async def test_exact_route_in_multiple_platforms(self):
        wrong_api = SimpleNamespace(_http=SimpleNamespace(request=AsyncMock()))
        self.platforms["other-instance"] = SimpleNamespace(client=SimpleNamespace(api=wrong_api))
        self.assertTrue(await self.send())
        self.context.get_platform_inst.assert_called_once_with("official-instance")
        wrong_api._http.request.assert_not_awaited()

    async def test_mismatched_metadata_rejected(self):
        for platform_id, kind in (("other-instance", "qq_official"), ("official-instance", "aiocqhttp")):
            self.platform.meta = lambda: SimpleNamespace(id=platform_id, name=kind)
            self.assertFalse(await self.send())
        self.api._http.request.assert_not_awaited()

    async def test_invalid_route(self):
        for route in ({}, {**self.route, "platform_type": "webchat"},
                      {**self.route, "group_id": ""}, {**self.route, "platform_id": 1}):
            self.assertFalse(await self.send(route))
        self.context.get_platform_inst.assert_not_called()

    async def test_missing_or_empty_file_no_upload(self):
        Path(self.png).unlink()
        self.assertFalse(await self.send())
        Path(self.png).touch()
        self.assertFalse(await self.send())
        self.api._http.request.assert_not_awaited()

    async def test_cancellation_propagates(self):
        self.api._http.request.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.send()

    async def test_onebot_uses_context_and_distinguishes_receipt(self):
        self.platform.meta = lambda: SimpleNamespace(id="official-instance", name="aiocqhttp")
        route = {**self.route, "platform_type": "aiocqhttp", "group_id": "123456"}
        with self.assertLogs(sender.logger, level="INFO") as logs:
            self.assertTrue(await self.send(route))
        self.assertIn("no QQ receipt verified", " ".join(logs.output))
        session, chain = self.context.send_message.call_args.args
        self.assertEqual(session.platform_id, "official-instance")
        self.assertEqual(session.session_id, "123456")
        self.assertIs(session.message_type, self.group_type)
        self.assertEqual((chain.text, chain.image), ("summary", self.png))
        self.api._http.request.assert_not_awaited()
        self.api.post_group_message.assert_not_awaited()
        self.context.send_message.return_value = False
        self.assertFalse(await self.send(route))
        self.context.send_message.side_effect = RuntimeError("TOKEN_PRIVATE_SENTINEL")
        with self.assertLogs(sender.logger, level="WARNING") as logs:
            self.assertFalse(await self.send(route))
        self.assertNotIn("TOKEN_PRIVATE_SENTINEL", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
