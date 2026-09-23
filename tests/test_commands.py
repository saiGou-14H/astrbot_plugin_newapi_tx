"""Command integration regressions; all state and quota responses are synthetic."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import aiosqlite
import httpx
from astrbot.api import AstrBotConfig
from astrbot.api.message_components import At
from astrbot.core.star.filter.command import CommandFilter


# Load only the source package path, not its plugin-starting __init__.py.
# Do not import a TestCase from test_core_identity: discovery would run it twice.
if "compat_plugin" not in sys.modules:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "compat_plugin", root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot locate compat_plugin")
    sys.modules["compat_plugin"] = importlib.util.module_from_spec(spec)

from compat_plugin.newapi_utils import NewApiCore
from compat_plugin.heist_logic import HeistLogic
from compat_plugin.main import NewApiSuitePlugin


class FakeEvent:
    def __init__(self, text="", sender="synthetic-sender", platform="qq_official", segments=None):
        self.text = text
        self.sender = sender
        self.platform = platform
        self.segments = list(segments or [])
        self.extra = {}
        self.is_at_or_wake_command = True
        self.role = "admin"
        self.message_str = text
        self.message_obj = SimpleNamespace(message_str=text, message=self.segments)

    def get_sender_id(self):
        return self.sender

    def get_self_id(self):
        return "qq_official" if self.platform == "qq_official" else "999999"

    def get_platform_name(self):
        return self.platform

    def get_group_id(self):
        return "synthetic-group"

    def get_messages(self):
        return self.segments

    def get_message_str(self):
        return self.text

    def plain_result(self, text):
        return text

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_extra(self, key=None, default=None):
        return self.extra if key is None else self.extra.get(key, default)

    def is_admin(self):
        return True


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        error_log = patch("compat_plugin.main.logger.error")
        error_log.start()
        self.addCleanup(error_log.stop)
        self.config = AstrBotConfig.__new__(AstrBotConfig)
        template = "CHECKED site={site_id} added={display_added} total={display_total}"
        self.config.update({
            "binding_settings": {"quota_display_ratio": 100, "enable_openid_binding": True,
                                 "binding_group": "default", "wild_bind_group_only": True},
            "group_whitelist_settings": {"enabled": False},
            "check_in_settings": {"enabled": True, "wild_bot_priority": False,
                                  "min_display_quota": 1.25, "max_display_quota": 1.25,
                                  "double_chance": 0, "first_check_in_bonus_enabled": False,
                                  "check_in_success_template": template,
                                  "check_in_doubled_template": template,
                                  "first_check_in_success_template": template},
            "heist_settings": {"enabled": True, "cooldown_seconds": 0,
                               "max_attempts_per_day": 3, "max_defenses_per_day": 3},
        })
        self.connection = await aiosqlite.connect(":memory:")
        self.addAsyncCleanup(self.connection.close)
        self.connection.row_factory = aiosqlite.Row
        for ddl in NewApiCore._TRANSFER_SQLITE_DDL.values():
            await self.connection.execute(ddl)
        await self.connection.commit()
        self.core = NewApiCore(self.config)
        self.core.db_mode = "sqlite"
        self.core.db_conn = self.connection
        self.core.api_request = AsyncMock(side_effect=self._mock_request)
        self.quota = {13: 1000, 26: 2000, 39: 3000}
        self.quota_calls = []

        guard = patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock,
                             side_effect=AssertionError("Real HTTP is forbidden"))
        blocked = guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(blocked.assert_not_awaited)

        self.plugin = object.__new__(NewApiSuitePlugin)
        self.plugin.config = self.config
        self.plugin.lang = "zh"
        self.plugin.core = self.core
        self.plugin.heist_handler = HeistLogic(self.config, self.core)
        self.plugin._balance_cache = {}
        self.plugin._kv_lock = asyncio.Lock()
        self.plugin._reply = lambda event, text: text
        self.kv = {}
        async def get_kv(key, default=None):
            return self.kv.get(key, default)
        async def put_kv(key, value):
            self.kv[key] = value
        self.plugin.get_kv_data = AsyncMock(side_effect=get_kv)
        self.plugin.put_kv_data = AsyncMock(side_effect=put_kv)
        self.plugin.delete_kv_data = AsyncMock(side_effect=lambda key: self.kv.pop(key, None))

    async def _mock_request(self, method, endpoint, json_data=None):
        if method == "GET" and endpoint.startswith("/api/user/"):
            site_id = int(endpoint.rsplit("/", 1)[1])
            return {"success": True, "data": {"id": site_id, "quota": self.quota[site_id],
                                             "group": "default", "username": "synthetic"}}
        if (method, endpoint) == ("POST", "/api/user/manage"):
            self.assertEqual(json_data["action"], "add_quota")
            self.quota_calls.append(dict(json_data))
            site_id = json_data["id"]
            if json_data["mode"] == "add":
                self.quota[site_id] += json_data["value"]
            elif json_data["mode"] == "subtract":
                self.quota[site_id] -= json_data["value"]
            else:
                self.fail("Unexpected quota mode")
            return {"success": True}
        self.fail(f"Unexpected mock API route: {method} {endpoint}")

    async def seed_openid(self, openid="synthetic-target", site_id=13):
        await self.connection.execute(
            "INSERT INTO newapi_openid_bindings (openid, website_user_id) VALUES (?, ?)",
            (openid, site_id),
        )
        await self.connection.commit()

    async def seed_qq(self, qq_id=70001, site_id=13):
        await self.connection.execute(
            "INSERT INTO newapi_bindings (qq_id, website_user_id) VALUES (?, ?)",
            (qq_id, site_id),
        )
        await self.connection.commit()

    async def collect(self, method, event, *args, **kwargs):
        replies = [reply async for reply in method(event, *args, **kwargs)]
        self.assertTrue(replies, "Command must produce a response")
        return "\n".join(replies)

    async def adjust(self, arguments, *, sender="synthetic-admin", platform="qq_official", segments=None):
        event = FakeEvent("调整余额 " + arguments, sender=sender, platform=platform, segments=segments)
        # Exercise the framework's real GreedyStr binding, not a handwritten split.
        command_filter = CommandFilter("调整余额")
        command_filter.init_handler_md(SimpleNamespace(handler=NewApiSuitePlugin.handle_adjust_balance))
        self.assertTrue(command_filter.filter(event, self.config))
        parameters = event.get_extra("parsed_params")
        self.assertIsInstance(parameters, dict)
        self.assertEqual(str(parameters["arguments"]), arguments.strip())
        return await self.collect(self.plugin.handle_adjust_balance, event, **parameters)

    async def test_adjust_site_id_positive_and_negative_via_greedy_parser(self):
        await self.seed_openid()
        for amount, mode, expected in (("+1.25", "add", 1125), ("-1.25", "subtract", 1000)):
            with self.subTest(amount=amount):
                reply = await self.adjust("13 " + amount)
                self.assertIn("操作成功", reply)
                self.assertEqual(self.quota_calls[-1], {"id": 13, "action": "add_quota", "mode": mode, "value": 125})
                self.assertEqual(self.quota[13], expected)
                self.assertEqual(self.plugin._balance_cache[13][1], expected)
        self.assertEqual(len(self.quota_calls), 2)

    async def test_adjust_openid_at_with_spaces_in_display_name(self):
        await self.seed_openid()
        reply = await self.adjust("@Display Name(synthetic-target) +1.25", segments=[
            At(qq="qq_official"), At(qq="synthetic-target", name="Display Name"),
        ])
        self.assertIn("操作成功", reply)
        self.assertEqual([call["id"] for call in self.quota_calls], [13])

    async def test_adjust_qq_at_never_resolves_colliding_site_id(self):
        await self.seed_openid(site_id=13)
        await self.seed_qq(qq_id=13, site_id=26)
        reply = await self.adjust("@QQ Name(13) -1.25", sender="70002", platform="aiocqhttp",
                                  segments=[At(qq="13", name="QQ Name")])
        self.assertIn("操作成功", reply)
        self.assertEqual([call["id"] for call in self.quota_calls], [26])
        self.assertEqual(self.quota[13], 1000)

    async def test_adjust_nonfinite_missing_zero_and_multiple_targets_never_write(self):
        await self.seed_openid()
        cases = [("13 " + value, []) for value in ("nan", "NaN", "inf", "-inf", "1e309", "0")]
        cases += [("", []), ("13", []), ("@Display Name(synthetic-target)", [At(qq="synthetic-target", name="Display Name")]),
                  ("@One @Two 1.25", [At(qq="synthetic-target"), At(qq="synthetic-other")])]
        for arguments, segments in cases:
            with self.subTest(arguments=arguments):
                self.core.api_request.reset_mock()
                reply = await self.adjust(arguments, segments=segments)
                self.assertTrue(reply)
                self.core.api_request.assert_not_awaited()
        self.assertEqual(self.quota_calls, [])

    async def test_query_other_balance_openid_only_site(self):
        await self.seed_openid()
        reply = await self.collect(self.plugin.handle_query_other_balance, FakeEvent(), "13")
        self.assertIn("13", reply)
        self.assertIn("10.000000", reply)
        self.assertEqual(self.quota_calls, [])

    async def test_lookup_openid_only_and_dual_binding(self):
        await self.seed_openid()
        reply = await self.collect(self.plugin.handle_universal_lookup, FakeEvent(), "13")
        self.assertIn("OpenID", reply)
        self.assertEqual(reply.count("synthetic-target"), 1)
        await self.seed_qq()
        reply = await self.collect(self.plugin.handle_universal_lookup, FakeEvent(), "13")
        self.assertIn("70001", reply)
        self.assertEqual(reply.count("synthetic-target"), 1)
        self.core.api_request.assert_not_awaited()

    async def test_check_in_official_numeric_openid_second_call_does_not_pay(self):
        # Numeric OpenID is still OpenID on the official platform.
        await self.seed_openid(openid="70001")
        await self.seed_qq(qq_id=70001, site_id=26)
        first = await self.collect(self.plugin.handle_check_in, FakeEvent(sender="70001"))
        second = await self.collect(self.plugin.handle_check_in, FakeEvent(sender="70001"))
        self.assertIn("CHECKED site=13", first)
        self.assertEqual(second, self.plugin.t("check_in.already"))
        self.assertEqual(self.quota_calls, [{"id": 13, "action": "add_quota", "mode": "add", "value": 125}])
        self.assertEqual(self.quota[26], 2000)

    async def test_heist_site_target_resolves_and_self_is_rejected(self):
        await self.seed_openid(openid="synthetic-target", site_id=13)
        await self.seed_openid(openid="synthetic-robber", site_id=26)
        handler = self.plugin.heist_handler
        status, parties = await handler._resolve_heist_parties("openid:synthetic-robber", "13")
        self.assertEqual(status, "VALID")
        self.assertEqual((parties["robber_site_id"], parties["victim_site_id"]), (26, 13))
        with patch.object(handler, "_determine_heist_outcome", return_value=("SUCCESS", 1.25)), \
             patch.object(self.core, "transfer_display_quota", new_callable=AsyncMock, return_value=(True, 1.25, 125)) as transfer, \
             patch.object(self.core, "log_heist_attempt", new_callable=AsyncMock, return_value=1) as log:
            reply = await self.collect(self.plugin.handle_heist_command, FakeEvent(sender="synthetic-robber"), "13")
            self.assertIn("成功", reply)
            transfer.assert_awaited_once_with(from_user_id=13, to_user_id=26, display_amount=1.25, allow_partial=True)
            log.assert_awaited_once()
            transfer.reset_mock()
            log.reset_mock()
            reply = await self.collect(self.plugin.handle_heist_command, FakeEvent(sender="synthetic-target"), "13")
            self.assertIn("不能打劫自己", reply)
            transfer.assert_not_awaited()
            log.assert_not_awaited()
        self.assertEqual(self.quota_calls, [])
        cursor = await self.connection.execute("SELECT COUNT(*) FROM daily_heist_log")
        self.assertEqual((await cursor.fetchone())[0], 0)
        await cursor.close()

    async def test_verify_token_passes_bound_site_id_and_rejects_mismatch(self):
        await self.seed_openid()
        for returned_id in (26, 13):
            with self.subTest(returned_id=returned_id):
                with patch.object(self.core, "get_self_by_user_token", new_callable=AsyncMock,
                                  return_value={"user_id": returned_id}) as verify, \
                     patch.object(self.plugin, "_mark_rp_verified", new_callable=AsyncMock) as mark:
                    reply = await self.collect(self.plugin.handle_verify_token, FakeEvent(sender="synthetic-target"), "synthetic-user-token")
                verify.assert_awaited_once_with("synthetic-user-token", expected_user_id=13)
                if returned_id == 13:
                    mark.assert_awaited_once_with(13)
                    self.assertEqual(reply, self.plugin.t("rp.verify.success", site_id=13))
                else:
                    mark.assert_not_awaited()
                    self.assertEqual(reply, self.plugin.t("rp.verify.failed"))

    async def test_qq_insert_failure_does_not_delete_existing_binding(self):
        with patch.object(self.core, "insert_binding", new_callable=AsyncMock,
                          side_effect=RuntimeError("synthetic insert failure")), \
             patch.object(self.core, "delete_binding", new_callable=AsyncMock) as delete:
            success, reply = await self.plugin._perform_binding_ritual(70001, 13)
        self.assertFalse(success)
        self.assertEqual(reply, self.plugin.t("bind.failed"))
        delete.assert_not_awaited()
        self.core.api_request.assert_not_awaited()

    async def test_openid_insert_failure_does_not_delete_existing_binding(self):
        with patch.object(self.core, "insert_openid_binding", new_callable=AsyncMock,
                          side_effect=RuntimeError("synthetic insert failure")), \
             patch.object(self.core, "delete_openid_binding", new_callable=AsyncMock) as delete:
            reply = await self.plugin._perform_openid_binding(FakeEvent(), "synthetic-target", 13)
        self.assertEqual(reply, self.plugin.t("bind.failed"))
        delete.assert_not_awaited()
        self.assertEqual(self.quota_calls, [])


if __name__ == "__main__":
    unittest.main()
