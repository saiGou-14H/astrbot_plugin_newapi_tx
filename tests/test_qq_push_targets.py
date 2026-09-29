"""Offline multi-group configuration/binding tests using the real AstrBot SDK.

No other test module is imported. KV persistence is a copy-on-read/write fake;
no plugin initializer, live database, scheduler service or network is used.
"""
import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
import httpx
from astrbot.api import AstrBotConfig
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    PatchedGroupMessage, QQOfficialPlatformAdapter,
)
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.permission import PermissionType, PermissionTypeFilter
from astrbot.core.star.star_handler import star_handlers_registry

if "compat_plugin" not in sys.modules:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "compat_plugin", root / "__init__.py", submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot locate compat_plugin")
    sys.modules["compat_plugin"] = importlib.util.module_from_spec(spec)

from compat_plugin.main import NewApiSuitePlugin, TihudaceScheduler
from compat_plugin.qq_push_targets import (
    BINDINGS_KEY, configured_targets, current_group_route, parse_group_session,
    resolve_targets,
)


def platform(platform_id="official-a", kind="qq_official"):
    return SimpleNamespace(meta=lambda: SimpleNamespace(id=platform_id, name=kind))


def route(platform_id="official-a", group_id="openid-a", kind="qq_official"):
    return dict(platform_id=platform_id, platform_type=kind, group_id=group_id)


class ConfigurationTests(unittest.TestCase):
    def test_strings_accept_numbers_and_explicit_instance_sessions(self):
        self.assertEqual(configured_targets({"push_targets": ["123", "official-a:GroupMessage:openid-a"]}),
                         (["123", "official-a:GroupMessage:openid-a"], 0))

    def test_legacy_enabled_dicts_share_the_same_key(self):
        self.assertEqual(configured_targets({"push_targets": [
            {"enabled": True, "group_id": "123"},
            {"enabled": False, "group_id": "456"},
            {"group_id": 789}, "123",
        ]}), (["123", "789"], 0))

    def test_separators_and_duplicates_preserve_first_seen_order(self):
        self.assertEqual(configured_targets({"push_targets": [
            "123,456，789;123；456\n999\t888 789", "999",
        ]}), (["123", "456", "789", "999", "888"], 0))

    def test_invalid_items_do_not_abort_other_targets(self):
        self.assertEqual(configured_targets({"push_targets": [
            None, True, {}, "0,-12,word,a:FriendMessage:x,a:GroupMessage:x:sender", "123",
        ]}), (["123"], 8))

    def test_clear_and_missing_config_have_no_targets(self):
        for conf in ({}, {"push_targets": []}, {"push_targets": ""}, {"push_targets": ["  "]}):
            with self.subTest(conf=conf):
                self.assertEqual(configured_targets(conf), ([], 0))

    def test_malformed_container_is_diagnostic(self):
        for value in (None, {}, True, 123):
            with self.subTest(value=value):
                self.assertEqual(configured_targets({"push_targets": value}), ([], 1))

    def test_session_parser_rejects_channels_private_and_isolated_suffix(self):
        for value in ("a:FriendMessage:123", "a:OtherMessage:123", "a:GroupMessage:x:y", ":GroupMessage:x", "a:GroupMessage:", "a:GroupMessage:x/y"):
            with self.subTest(value=value):
                self.assertIsNone(parse_group_session(value))

    def test_official_number_requires_binding(self):
        self.assertEqual(resolve_targets({"push_targets": ["123"]}, {}, [platform()]),
                         ([], [("123", "unbound_group_number")]))

    def test_bindings_are_not_subscriptions(self):
        self.assertEqual(resolve_targets({"push_targets": []}, {"123": route()}, [platform()]), ([], []))

    def test_selected_aliases_and_explicit_session_merge_same_group(self):
        self.assertEqual(resolve_targets({"push_targets": ["123", "456", "official-a:GroupMessage:openid-a"]},
                                         {"123": route(), "456": route()}, [platform()]), ([route()], []))

    def test_platform_instance_is_exact_not_adapter_type(self):
        conf = {"push_targets": ["official-b:GroupMessage:openid-a", "qq_official:GroupMessage:openid-a"]}
        routes, issues = resolve_targets(conf, {}, [platform(), platform("official-b")])
        self.assertEqual(routes, [route("official-b")])
        self.assertEqual(issues, [("qq_official:GroupMessage:openid-a", "platform_unavailable")])

    def test_same_group_on_two_instances_remains_two_routes(self):
        routes, issues = resolve_targets({"push_targets": ["official-a:GroupMessage:openid-a", "official-b:GroupMessage:openid-a"]},
                                         {}, [platform(), platform("official-b")])
        self.assertEqual(routes, [route(), route("official-b")])
        self.assertEqual(issues, [])

    def test_multiple_platforms_do_not_guess_numeric_route(self):
        for platforms in ([platform(), platform("official-b")],
                          [platform("ob-a", "aiocqhttp"), platform("ob-b", "aiocqhttp")],
                          [platform(), platform("ob-a", "aiocqhttp")]):
            with self.subTest(platforms=platforms):
                self.assertEqual(resolve_targets({"push_targets": ["123"]}, {}, platforms),
                                 ([], [("123", "unbound_group_number")]))

    def test_single_onebot_accepts_direct_number(self):
        self.assertEqual(resolve_targets({"push_targets": ["123"]}, {}, [platform("ob-a", "aiocqhttp")]),
                         ([route("ob-a", "123", "aiocqhttp")], []))

    def test_duplicate_instance_ids_are_ambiguous(self):
        self.assertEqual(resolve_targets({"push_targets": ["official-a:GroupMessage:openid-a"]}, {}, [platform(), platform()]),
                         ([], [("official-a:GroupMessage:openid-a", "platform_unavailable")]))

    def test_stale_binding_does_not_fallback_to_another_instance(self):
        self.assertEqual(resolve_targets({"push_targets": ["123"]}, {"123": route()}, [platform("official-b")]),
                         ([], [("123", "platform_unavailable")]))

    def test_changed_adapter_type_invalidates_binding(self):
        self.assertEqual(resolve_targets({"push_targets": ["123"]}, {"123": route()}, [platform("official-a", "aiocqhttp")]),
                         ([], [("123", "platform_unavailable")]))

    def test_bad_binding_and_target_do_not_block_good_route(self):
        routes, issues = resolve_targets({"push_targets": ["123", "bad", "456"]},
                                         {"123": {"platform_id": "official-a"}, "456": route()}, [platform()])
        self.assertEqual(routes, [route()])
        self.assertCountEqual(issues, [("", "invalid_configuration"), ("123", "invalid_binding")])

    def test_onebot_rejects_non_numeric_explicit_group_id(self):
        self.assertEqual(resolve_targets({"push_targets": ["ob-a:GroupMessage:openid-a"]}, {}, [platform("ob-a", "aiocqhttp")]),
                         ([], [("ob-a:GroupMessage:openid-a", "invalid_group_id")]))


class BindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Guard both HTTP stacks plus raw sockets. Unexpected I/O is a test failure,
        # even if a production error handler catches the raised exception.
        for target in ("httpx.AsyncClient.request", "aiohttp.ClientSession._request"):
            guard = patch(target, new_callable=AsyncMock, side_effect=AssertionError("Network forbidden"))
            mocked = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(mocked.assert_not_called)
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.socket.sendto"):
            guard = patch(target, side_effect=AssertionError("Network forbidden"))
            mocked = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(mocked.assert_not_called)
        self.adapter = QQOfficialPlatformAdapter({
            "id": "official-a", "appid": "synthetic-app", "secret": "synthetic-secret",
            "enable_group_c2c": True, "enable_guild_direct_message": False,
        }, {}, asyncio.Queue())
        self.addAsyncCleanup(self.adapter.client.close)
        self.store = {}
        self.plugin = self.new_plugin()

    def new_plugin(self):
        plugin = object.__new__(NewApiSuitePlugin)
        plugin.config = AstrBotConfig.__new__(AstrBotConfig)
        plugin.config.update({"tihudace_settings": {"push_targets": [], "scheduled_enabled": True,
                                                   "interval_minutes": 60}})
        plugin.lang = "zh"
        plugin._kv_lock = asyncio.Lock()
        plugin._reply = lambda event, text: text
        plugin.context = SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[self.adapter]))

        async def get(key, default=None):
            snapshot = deepcopy(self.store.get(key, default))
            await asyncio.sleep(0)  # Deliberately expose read/modify/write races.
            return snapshot

        async def put(key, value):
            await asyncio.sleep(0)
            self.store[key] = deepcopy(value)

        plugin.get_kv_data = AsyncMock(side_effect=get)
        plugin.put_kv_data = AsyncMock(side_effect=put)
        return plugin

    async def event(self, group="openid-a", admin=True, text="醍醐推送绑定 123"):
        raw = PatchedGroupMessage(None, "offline-event", {
            "id": "offline-message", "group_openid": group,
            "author": {"member_openid": "sender-private", "username": "offline"},
            "content": text, "attachments": [], "mentions": [],
            "timestamp": "2026-01-01T00:00:00Z",
        })
        message = await QQOfficialPlatformAdapter._parse_from_qqofficial(raw, MessageType.GROUP_MESSAGE)
        message.group_id = group
        message.session_id = group + "_sender-private"
        event = self.adapter.create_event(message)
        event.role = "admin" if admin else "member"
        event.is_at_or_wake_command = True
        self.assertEqual(event.is_admin(), admin)
        return event

    async def collect(self, generator):
        return [item async for item in generator]

    async def bind(self, number="123", group="openid-a", plugin=None):
        return await self.collect((plugin or self.plugin).handle_tihudace_push_bind(await self.event(group), number))

    async def test_session_uses_raw_group_and_actual_instance_not_sender_session(self):
        event = await self.event()
        self.assertEqual(current_group_route(event), route())
        replies = await self.collect(self.plugin.handle_tihudace_push_session(event))
        self.assertIn("official-a:GroupMessage:openid-a", replies[0])
        self.assertNotIn("sender-private", replies[0])
        self.plugin.put_kv_data.assert_not_awaited()

    async def test_admin_binds_persistent_route_without_subscribing(self):
        replies = await self.bind()
        self.assertEqual(self.store, {BINDINGS_KEY: {"123": route()}})
        self.assertEqual(resolve_targets(self.plugin.config["tihudace_settings"], self.store[BINDINGS_KEY], [self.adapter]), ([], []))
        self.assertTrue(replies)

    async def test_member_cannot_bind_unbind_or_read_admin_status(self):
        self.store[BINDINGS_KEY] = {"123": route()}
        event = await self.event(admin=False)
        for method, args in ((self.plugin.handle_tihudace_push_bind, ("456",)),
                             (self.plugin.handle_tihudace_push_unbind, ("123",)),
                             (self.plugin.handle_tihudace_push_status, ())):
            with self.subTest(method=method.__name__):
                replies = await self.collect(method(event, *args))
                self.assertTrue(replies)
        self.assertEqual(self.store[BINDINGS_KEY], {"123": route()})
        self.plugin.get_kv_data.assert_not_awaited()
        self.plugin.put_kv_data.assert_not_awaited()

    async def test_real_registered_command_and_permission_filters(self):
        for suffix, text in (("bind", "醍醐推送绑定 123"), ("unbind", "醍醐推送解绑 123"), ("status", "醍醐推送状态")):
            with self.subTest(command=suffix):
                method = getattr(NewApiSuitePlugin, "handle_tihudace_push_" + suffix)
                registered = [md for md in star_handlers_registry if md.handler is method]
                self.assertEqual(len(registered), 1)
                filters = registered[0].event_filters
                permissions = [f for f in filters if isinstance(f, PermissionTypeFilter)]
                commands = [f for f in filters if isinstance(f, CommandFilter)]
                self.assertEqual(len(permissions), 1)
                self.assertEqual(permissions[0].permission_type, PermissionType.ADMIN)
                self.assertEqual(len(commands), 1)
                for admin in (False, True):
                    event = await self.event(admin=admin, text=text)
                    self.assertTrue(commands[0].filter(event, self.plugin.config))
                    self.assertEqual(permissions[0].filter(event, self.plugin.config), admin)

    async def test_channel_and_forged_raw_object_cannot_bind(self):
        event = await self.event()
        # Guild channels also report GROUP_MESSAGE, but are not botpy.GroupMessage.
        event.message_obj.raw_message = SimpleNamespace(group_openid="openid-a", channel_id="channel-a")
        self.assertIsNone(current_group_route(event))
        await self.collect(self.plugin.handle_tihudace_push_bind(event, "123"))
        self.plugin.put_kv_data.assert_not_awaited()
        replies = await self.collect(self.plugin.handle_tihudace_push_session(event))
        self.assertNotIn("official-a:GroupMessage:", replies[0])

    async def test_private_message_cannot_bind(self):
        event = await self.event()
        with patch.object(event, "get_message_type", return_value=MessageType.FRIEND_MESSAGE):
            self.assertIsNone(current_group_route(event))
            await self.collect(self.plugin.handle_tihudace_push_bind(event, "123"))
        self.plugin.put_kv_data.assert_not_awaited()

    async def test_raw_group_openid_must_match_event_group(self):
        event = await self.event()
        event.message_obj.group_id = "different-group"
        self.assertIsNone(current_group_route(event))
        await self.collect(self.plugin.handle_tihudace_push_bind(event, "123"))
        self.plugin.put_kv_data.assert_not_awaited()

    async def test_bad_group_number_cannot_write(self):
        for number in ("", "0", "-123", "abc", "123,456", "official-a:GroupMessage:openid-a"):
            with self.subTest(number=number):
                await self.bind(number)
        self.plugin.put_kv_data.assert_not_awaited()

    async def test_onebot_binding_requires_actual_numeric_group_match(self):
        event = await self.event(group="123")
        with patch.object(event, "get_platform_name", return_value="aiocqhttp"):
            await self.collect(self.plugin.handle_tihudace_push_bind(event, "456"))
            self.plugin.put_kv_data.assert_not_awaited()
            await self.collect(self.plugin.handle_tihudace_push_bind(event, "123"))
        self.assertEqual(self.store[BINDINGS_KEY], {"123": route(group_id="123", kind="aiocqhttp")})

    async def test_conflict_refuses_overwrite_until_unbound(self):
        await self.bind()
        self.plugin.put_kv_data.reset_mock()
        await self.bind(group="openid-b")
        self.plugin.put_kv_data.assert_not_awaited()
        self.assertEqual(self.store[BINDINGS_KEY]["123"], route())
        await self.collect(self.plugin.handle_tihudace_push_unbind(await self.event(), "123"))
        await self.bind(group="openid-b")
        self.assertEqual(self.store[BINDINGS_KEY]["123"], route(group_id="openid-b"))

    async def test_same_binding_is_idempotent(self):
        await self.bind()
        await self.bind()
        self.assertEqual(self.store[BINDINGS_KEY], {"123": route()})

    async def test_binding_survives_new_plugin_instance_from_persistent_fake(self):
        await self.bind()
        replacement = self.new_plugin()
        self.assertIsNot(replacement._kv_lock, self.plugin._kv_lock)
        loaded = await replacement.get_kv_data(BINDINGS_KEY, {})
        self.assertIsNot(loaded, self.store[BINDINGS_KEY])
        self.assertEqual(resolve_targets({"push_targets": ["123"]}, loaded, [self.adapter]), ([route()], []))
        loaded["123"]["group_id"] = "mutated-copy"
        self.assertEqual(self.store[BINDINGS_KEY]["123"], route())

    async def test_concurrent_different_groups_do_not_lose_update(self):
        await asyncio.gather(self.bind("123", "openid-a"), self.bind("456", "openid-b"))
        self.assertEqual(self.store[BINDINGS_KEY], {"123": route(), "456": route(group_id="openid-b")})
        self.assertEqual(self.plugin.put_kv_data.await_count, 2)

    async def test_corrupt_store_is_not_silently_overwritten(self):
        self.store[BINDINGS_KEY] = ["corrupt"]
        await self.bind()
        await self.collect(self.plugin.handle_tihudace_push_unbind(await self.event(), "123"))
        self.plugin.put_kv_data.assert_not_awaited()
        self.assertEqual(self.store[BINDINGS_KEY], ["corrupt"])

    async def test_unbinding_changes_subsequent_resolution(self):
        await self.bind()
        conf = {"push_targets": ["123"]}
        self.assertEqual(resolve_targets(conf, await self.plugin.get_kv_data(BINDINGS_KEY), [self.adapter])[0], [route()])
        await self.collect(self.plugin.handle_tihudace_push_unbind(await self.event(), "123"))
        self.assertEqual(resolve_targets(conf, await self.plugin.get_kv_data(BINDINGS_KEY), [self.adapter]),
                         ([], [("123", "unbound_group_number")]))

    async def test_scheduler_reloads_allowlist_and_bindings_each_run(self):
        await self.bind()
        await self.bind("456", "openid-b")
        conf = self.plugin.config["tihudace_settings"]
        conf["push_targets"] = ["123", "official-a:GroupMessage:openid-a", "invalid"]
        self.plugin._generate_tihudace_png = AsyncMock(return_value=("OK", "/tmp/not-real.png"))
        scheduler = TihudaceScheduler(self.plugin, self.plugin.context)
        scheduler._send_qq_group = AsyncMock(return_value=True)
        await scheduler.run_push_job()
        self.assertEqual(scheduler._send_qq_group.await_count, 1)
        self.assertEqual(scheduler._send_qq_group.call_args.args[0], route())
        scheduler._send_qq_group.reset_mock()
        conf["push_targets"] = []
        await scheduler.run_push_job()
        scheduler._send_qq_group.assert_not_awaited()
        self.assertEqual(set(self.store[BINDINGS_KEY]), {"123", "456"})
        conf["push_targets"] = ["123"]
        await self.collect(self.plugin.handle_tihudace_push_unbind(await self.event(), "123"))
        await scheduler.run_push_job()
        scheduler._send_qq_group.assert_not_awaited()
        conf["push_targets"] = ["456"]
        await scheduler.run_push_job()
        scheduler._send_qq_group.assert_awaited_once()
        self.assertEqual(scheduler._send_qq_group.call_args.args[0], route(group_id="openid-b"))

    async def test_status_reports_selected_resolved_and_unbound_targets(self):
        await self.bind()
        self.plugin.config["tihudace_settings"]["push_targets"] = ["123", "456"]
        scheduler = TihudaceScheduler(self.plugin, self.plugin.context)
        scheduler.last_status = 'MODEL_TIMEOUT'
        scheduler.last_elapsed = 600.0
        self.plugin._tihudace_scheduler = scheduler
        replies = await self.collect(self.plugin.handle_tihudace_push_status(await self.event()))
        self.assertIn('模型生成超时，下个周期继续', replies[0])
        self.assertIn('600.0 秒', replies[0])
        self.assertEqual(len(replies), 1)
        self.assertIn("123", replies[0])
        self.assertIn("456", replies[0])
        self.assertIn("群号尚未绑定", replies[0])
        self.assertIn("醍醐推送绑定", replies[0])
        self.assertNotIn("sender-private", replies[0])


if __name__ == "__main__":
    unittest.main()
