"""Offline QQ callback/commit integration; synthetic identities and no network.

Do not import test_commands.TestCase: unittest would collect it a second time.
"""
import asyncio
import copy
import importlib.util
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import botpy
import httpx
from astrbot.api import AstrBotConfig
from astrbot.api.message_components import At, Plain
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    QQOfficialPlatformAdapter, PatchedGroupMessage, botClient,
)

if "compat_plugin" not in sys.modules:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "compat_plugin", root / "__init__.py", submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot locate compat_plugin")
    sys.modules["compat_plugin"] = importlib.util.module_from_spec(spec)

from compat_plugin.main import NewApiSuitePlugin
from compat_plugin.qq_compat import (
    QQMentionCompat, known_mentions, query_group_receive_mode, _CONTEXT,
)

SENDER = "SENDER_PRIVATE_SENTINEL"
TARGET = "TARGET_PRIVATE_SENTINEL"
ALIAS = "ALIAS_PRIVATE_SENTINEL"
BOT = "BOT_PRIVATE_SENTINEL"
GROUP = "GROUP_PRIVATE_SENTINEL"
SECRET = "AUTH_PRIVATE_SENTINEL"
BODY = "BODY_PRIVATE_SENTINEL"


def payload(content="查询 <@ALIAS_PRIVATE_SENTINEL>", mentions=True):
    data = {
        "id": "MESSAGE_PRIVATE_SENTINEL", "group_openid": GROUP,
        "author": {"member_openid": SENDER, "username": "NAME_PRIVATE_SENTINEL"},
        "content": content, "attachments": [], "timestamp": "2026-01-01T00:00:00Z",
    }
    if mentions is True:
        data["mentions"] = [
            {"id": BOT, "member_openid": "BOT_CANONICAL", "is_you": True},
            {"id": ALIAS, "member_openid": TARGET, "username": "TARGET_NAME_PRIVATE_SENTINEL"},
        ]
    elif mentions is not None:
        data["mentions"] = mentions
    return data


class QQCompatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Defense in depth: unexpected library HTTP and socket connections fail.
        for owner, name in ((httpx.AsyncClient, "request"), (aiohttp.ClientSession, "_request")):
            guard = patch.object(owner, name, new_callable=AsyncMock,
                                 side_effect=AssertionError("Real HTTP forbidden"))
            mock = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(mock.assert_not_awaited)
        guard = patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        self.events = []
        self.platform = QQOfficialPlatformAdapter({
            "id": "offline", "appid": "synthetic-app", "secret": "synthetic-secret",
            "enable_group_c2c": True, "enable_guild_direct_message": False,
        }, {}, asyncio.Queue())
        self.client = self.platform.get_client()
        self.addAsyncCleanup(self.client.close)
        # Only the queue boundary is mocked; create_event and _commit remain real.
        self.platform.commit_event = Mock(side_effect=self.events.append)
        self.logger = Mock()
        self.compat = QQMentionCompat(self.logger)
        self.addCleanup(self.compat.close)
        self.config = AstrBotConfig.__new__(AstrBotConfig)
        self.config.update({"group_whitelist_settings": {"enabled": False},
                            "binding_settings": {"quota_display_ratio": 100}})
        self.plugin = object.__new__(NewApiSuitePlugin)
        self.plugin.config = self.config
        self.plugin.lang = "zh"
        self.plugin._reply = lambda event, text: text
        self.plugin.core = SimpleNamespace(
            lookup_binding=AsyncMock(return_value=("OPENID", {"website_user_id": 13})),
            get_api_user_data=AsyncMock(return_value={"quota": 1000}),
            get_user_by_website_id=AsyncMock(return_value=None),
            get_openid_by_website_id=AsyncMock(return_value={"openid": TARGET, "binding_time": "synthetic"}),
            adjust_balance_by_identifier=AsyncMock(return_value=("SUCCESS", {
                "website_user_id": 13, "new_display_quota": 11.25})),
        )
        self.plugin._refresh_balance_cache = AsyncMock()

    async def deliver(self, data=None, kind="group_at_message_create"):
        data = payload() if data is None else data
        raw = PatchedGroupMessage(self.client.api, "synthetic-event", data)
        await getattr(self.client, "on_" + kind)(raw)
        return raw, self.events[-1]

    def member_ids(self, event):
        return [str(s.qq) for s in event.get_messages() if isinstance(s, At)
                and str(s.qq) not in (BOT, "qq_official", "BOT_CANONICAL")]

    def parse_command(self, event, name, method):
        # Wakeup/permission policy is outside CommandFilter, explicitly supplied here.
        event.is_at_or_wake_command = True
        event.role = "admin"
        command = CommandFilter(name)
        command.init_handler_md(SimpleNamespace(handler=method))
        self.assertTrue(command.filter(event, self.config))
        return event.get_extra("parsed_params")

    async def test_both_callbacks_commit_create_event_preserve_raw_and_sender(self):
        self.compat.install(self.client)
        for kind in ("group_at_message_create", "group_message_create"):
            with self.subTest(kind=kind):
                data = payload(f'<@{BOT}> 查询 <@{ALIAS}> <qqbot-at-user id="{ALIAS}"/>')
                before = copy.deepcopy(data)
                raw, event = await self.deliver(data, kind)
                self.assertIs(event.message_obj.raw_message, raw)
                self.assertIs(raw.raw_data, data)
                self.assertEqual(data, before)
                self.assertEqual(raw.content, before["content"])
                self.assertEqual(event.get_sender_id(), SENDER)
                self.assertEqual(event.get_group_id(), GROUP)
                self.assertEqual(event.message_obj.message_id, data["id"])
                self.assertEqual(self.member_ids(event), [TARGET])
                self.assertEqual(event.get_message_str(), "查询")
                summary = event.message_obj._newapi_mention_diagnostic
                self.assertEqual(summary["event"], kind.upper())
                self.assertEqual(summary["member_at_added"], 1)
        self.assertEqual(self.platform.commit_event.call_count, 2)

    async def test_native_parser_dispatch_works_without_plugin_or_login_patch(self):
        tasks = []
        original_login = self.client._bot_login.__func__
        # Execute the real login lifecycle; only its two HTTP leaf calls are mocks.
        with patch.object(self.client.http, "login", new_callable=AsyncMock,
                          return_value={"id": "123", "username": "synthetic"}), \
             patch.object(self.client.api, "get_ws_url", new_callable=AsyncMock,
                          return_value={"session_start_limit": {"max_concurrency": 1}}):
            await self.client._bot_login(SimpleNamespace())
        state = self.client._connection.state
        schedule = self.client._schedule_event
        def track(*args, **kwargs):
            task = schedule(*args, **kwargs)
            tasks.append(task)
            return task
        with patch.object(self.client, "_schedule_event", side_effect=track):
            for kind in ("group_at_message_create", "group_message_create"):
                self.assertIn(kind, state.parsers)
                state.parsers[kind]({"id": "synthetic-event", "d": payload()})
            await asyncio.gather(*tasks)
        self.assertEqual(len(self.events), 2)
        self.assertEqual(self.member_ids(self.events[0]), [])
        parsers_before = dict(state.parsers)
        self.compat.install(self.client)
        self.assertEqual(state.parsers, parsers_before)
        self.assertIs(self.client._bot_login.__func__, original_login)
        self.assertNotIn("_bot_login", self.client.__dict__)

    async def test_real_command_filters_and_mocked_economy(self):
        self.compat.install(self.client)
        for name, handler, extra in (
            ("查余额", self.plugin.handle_query_other_balance, ""),
            ("查询", self.plugin.handle_universal_lookup, ""),
            ("调整余额", self.plugin.handle_adjust_balance, " 1.25"),
            ("提及诊断", self.plugin.handle_mention_diagnostic, ""),
        ):
            with self.subTest(command=name):
                _, event = await self.deliver(payload(f'{name} <qqbot-at-user id="{ALIAS}"/>{extra}'))
                params = self.parse_command(event, name, getattr(NewApiSuitePlugin, handler.__name__))
                with patch("compat_plugin.main.query_group_receive_mode", new_callable=AsyncMock,
                           return_value="only_mention（仅提及）"):
                    result = [reply async for reply in handler(event, **params)]
                self.assertTrue(result)
                self.assertNotIn("未预期", "".join(result))
                if name == "提及诊断":
                    self.assertIn("最终目标数：1", result[0])
                    for sentinel in (SENDER, TARGET, ALIAS, BOT, GROUP):
                        self.assertNotIn(sentinel, result[0])
        self.plugin.core.adjust_balance_by_identifier.assert_awaited_once_with("openid:" + TARGET, 1.25)
        self.plugin.core.lookup_binding.assert_any_await("openid:" + TARGET)

    async def test_no_mentions_does_not_guess_author_or_content_target(self):
        self.compat.install(self.client)
        for mentions in (None, [], [{"username": TARGET}]):
            _, event = await self.deliver(payload(f"查询 <@{TARGET}>", mentions))
            self.assertEqual(self.member_ids(event), [])
            self.assertEqual(self.plugin._extract_at_targets(event), [])
            self.assertIn(f"<@{TARGET}>", event.get_message_str())

    async def test_scope_leaves_other_commands_and_quoted_mentions_alone(self):
        self.compat.install(self.client)
        for text in ("普通聊天", "查询余额", "前缀 查询", "签到"):
            _, event = await self.deliver(payload(text + f" <@{ALIAS}>"))
            self.assertEqual(self.member_ids(event), [])
            self.assertFalse(hasattr(event.message_obj, "_newapi_mention_diagnostic"))
        data = payload("查询", [])
        data["msg_elements"] = [{"mentions": [{"id": TARGET}], "content": f"<@{TARGET}>"}]
        _, event = await self.deliver(data)
        self.assertEqual(self.member_ids(event), [])
        self.assertFalse(self.compat.install(SimpleNamespace()))

    async def test_bot_aliases_and_numeric_openid_deduplicate(self):
        self.compat.install(self.client)
        numeric = "0" * 31 + "1"
        entries = [{"id": BOT, "member_openid": "bot-alias", "bot": True},
                   {"id": "bot-alias"}, {"id": "numeric-alias", "member_openid": numeric},
                   {"id": numeric}]
        _, event = await self.deliver(payload('查询 <@bot-alias> <@numeric-alias>', entries))
        self.assertEqual(self.member_ids(event), [numeric])
        self.assertEqual(self.plugin._extract_at_targets(event), ["openid:" + numeric])

    async def test_repeated_install_close_and_new_owner(self):
        names = ("on_group_at_message_create", "on_group_message_create", "_commit")
        originals = {name: getattr(self.client, name).__func__ for name in names}
        self.assertTrue(self.compat.install(self.client))
        wrappers = {name: getattr(self.client, name) for name in names}
        self.assertFalse(self.compat.install(self.client))
        for name in names:
            self.assertIs(getattr(self.client, name), wrappers[name])
        self.compat.close()
        self.compat.close()
        for name in names:
            self.assertNotIn(name, self.client.__dict__)
            self.assertIs(getattr(self.client, name).__func__, originals[name])
        self.assertFalse(self.compat.install(self.client))
        replacement = QQMentionCompat(self.logger)
        self.addCleanup(replacement.close)
        self.assertTrue(replacement.install(self.client))
        _, event = await self.deliver()
        self.assertEqual(self.member_ids(event), [TARGET])

    async def test_third_party_wrapper_is_not_overwritten_and_old_hook_inert(self):
        self.compat.install(self.client)
        previous = self.client._commit
        third_party = Mock(side_effect=previous)
        self.client._commit = third_party
        self.compat.close()
        self.assertIs(self.client._commit, third_party)
        _, event = await self.deliver()
        third_party.assert_called_once()
        self.assertEqual(self.member_ids(event), [])

    async def test_preexisting_instance_overrides_restored(self):
        original = self.client._commit
        override = Mock(side_effect=original)
        self.client._commit = override
        self.compat.install(self.client)
        self.compat.close()
        self.assertIs(self.client._commit, override)

    async def test_concurrent_callbacks_keep_context_isolated(self):
        self.compat.install(self.client)
        original = QQOfficialPlatformAdapter._parse_from_qqofficial
        entered = asyncio.Event()
        release = asyncio.Event()
        async def delayed(raw, *args, **kwargs):
            if raw.group_openid == "first-group":
                entered.set()
                await release.wait()
            return await original(raw, *args, **kwargs)
        first = payload("查询", [{"id": "first-target"}])
        first["group_openid"] = "first-group"
        second = payload("查询", [{"id": "second-target"}])
        with patch.object(QQOfficialPlatformAdapter, "_parse_from_qqofficial", side_effect=delayed):
            task = asyncio.create_task(self.deliver(first, "group_at_message_create"))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                await self.deliver(second, "group_message_create")
            finally:
                release.set()
                await task
        by_group = {event.get_group_id(): event for event in self.events}
        self.assertEqual(self.member_ids(by_group["first-group"]), ["first-target"])
        self.assertEqual(self.member_ids(by_group[GROUP]), ["second-target"])
        self.assertEqual(by_group["first-group"].message_obj._newapi_mention_diagnostic["event"], "GROUP_AT_MESSAGE_CREATE")
        self.assertEqual(by_group[GROUP].message_obj._newapi_mention_diagnostic["event"], "GROUP_MESSAGE_CREATE")
        self.assertIsNone(_CONTEXT.get())

    async def test_callback_exception_resets_context_without_replay(self):
        self.compat.install(self.client)
        with patch.object(QQOfficialPlatformAdapter, "_parse_from_qqofficial", side_effect=ValueError("synthetic")) as parse:
            with self.assertRaises(ValueError):
                await self.deliver()
            self.assertEqual(parse.call_count, 1)
        self.assertIsNone(_CONTEXT.get())
        self.platform.commit_event.assert_not_called()

    async def test_logs_only_fixed_fields_counts_and_bounded(self):
        self.compat.install(self.client)
        for _ in range(65):
            await self.deliver(payload(f"提及诊断 <@{ALIAS}> {BODY}"))
        logs = "\n".join(str(call) for call in self.logger.mock_calls)
        for sentinel in (SENDER, TARGET, ALIAS, BOT, GROUP, SECRET, BODY, "NAME_PRIVATE_SENTINEL", "MESSAGE_PRIVATE_SENTINEL"):
            self.assertNotIn(sentinel, logs)
        self.assertEqual(self.logger.info.call_count, 61)

    async def test_receive_mode_mock_session_get_only(self):
        _, event = await self.deliver()
        token = SimpleNamespace(access_token=SECRET, app_id="synthetic-app", get_string=lambda: SECRET)
        event.bot.http._token = token
        cases = [(200, {"recv_msg_setting": "all"}, "all（"),
                 (200, {"recv_msg_setting": "only_mention"}, "only_mention（"),
                 (200, {"recv_msg_setting": "mention_and_context"}, "mention_and_context（"),
                 (403, {"code": 11253, "message": BODY}, "11253"),
                 (200, {"code": "11253"}, "11253"),
                 (500, {"message": BODY}, "无法查询"),
                 (200, [BODY], "响应格式异常"),
                 (200, {"recv_msg_setting": BODY}, "未知")]
        for status, body, expected in cases:
            with self.subTest(status=status, body_type=type(body).__name__):
                response = SimpleNamespace(status=status, json=AsyncMock(return_value=body))
                request_cm = AsyncMock()
                request_cm.__aenter__.return_value = response
                session = Mock(spec=["get"])
                session.get.return_value = request_cm
                session_cm = AsyncMock()
                session_cm.__aenter__.return_value = session
                with patch("aiohttp.ClientSession", return_value=session_cm) as factory:
                    result = await query_group_receive_mode(event)
                factory.assert_called_once()
                session.get.assert_called_once()
                self.assertIn(expected, result)
                self.assertNotIn(BODY, result)
                self.assertNotIn(SECRET, result)
                args, kwargs = session.get.call_args
                self.assertTrue(args[0].endswith("/bot_state"))
                self.assertFalse(kwargs["allow_redirects"])
                self.assertEqual(kwargs["headers"]["Authorization"], SECRET)
                self.assertEqual([call[0] for call in session.mock_calls if '.' not in call[0]], ["get"])
        with patch("aiohttp.ClientSession", side_effect=RuntimeError(BODY + SECRET)):
            result = await query_group_receive_mode(event)
        self.assertIn("网络或响应异常", result)
        self.assertNotIn(BODY, result)
        self.assertNotIn(SECRET, result)

    async def test_multiple_targets_never_reach_economic_write(self):
        self.compat.install(self.client)
        _, event = await self.deliver(payload("调整余额 1.25", [{"id": "first"}, {"id": "second"}]))
        params = self.parse_command(event, "调整余额", NewApiSuitePlugin.handle_adjust_balance)
        result = [reply async for reply in self.plugin.handle_adjust_balance(event, **params)]
        self.assertEqual(result, [self.plugin.t("target.too_many")])
        self.plugin.core.adjust_balance_by_identifier.assert_not_awaited()

    async def test_cancelled_callback_clears_context(self):
        self.compat.install(self.client)
        with patch.object(QQOfficialPlatformAdapter, "_parse_from_qqofficial",
                          side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.deliver()
        self.assertIsNone(_CONTEXT.get())
        self.platform.commit_event.assert_not_called()

    async def test_new_client_can_install_without_touching_old_client(self):
        self.compat.install(self.client)
        second = botClient(intents=botpy.Intents(public_messages=True), bot_log=False)
        self.addAsyncCleanup(second.close)
        self.assertTrue(self.compat.install(second))
        self.assertEqual(len(self.compat.clients), 2)
        self.compat.close()
        self.assertNotIn("_commit", self.client.__dict__)
        self.assertNotIn("_commit", second.__dict__)

    async def test_receive_mode_missing_auth_never_opens_session(self):
        _, event = await self.deliver()
        event.bot.http._token = None
        with patch("aiohttp.ClientSession") as factory:
            self.assertIn("无法查询", await query_group_receive_mode(event))
        factory.assert_not_called()

    def test_conflicting_alias_stays_unresolved(self):
        _, aliases = known_mentions({"mentions": [
            {"id": "shared", "member_openid": "first"},
            {"id": "shared", "member_openid": "second"},
            {"id": "shared", "member_openid": "first"},
        ]})
        self.assertIsNone(aliases["shared"])


if __name__ == "__main__":
    unittest.main()
