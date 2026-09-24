"""Offline regression for QQ bare commands on the real AstrBot 4.28.1 stages.

Run with unittest discovery inside the prepared AstrBot environment. No deployed
configuration, plugin core, credentials, database, SDK login or provider is used.
The handler registry and session-policy boundary are isolated in-memory fixtures;
WakingCheckStage, ProcessStage, StarRequestSubStage, command/permission filters,
QQ parsing and event construction are native. We do not import another TestCase.
"""
import asyncio
import importlib
from pathlib import Path
import socket
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import httpx
from astrbot.api.message_components import At, AtAll, Plain, Reply
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    PatchedGroupMessage,
    QQOfficialPlatformAdapter,
)
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.filter.permission import PermissionType, PermissionTypeFilter
from astrbot.core.star.star_handler import EventType

wake_module = importlib.import_module("astrbot.core.pipeline.waking_check.stage")
process_module = importlib.import_module("astrbot.core.pipeline.process_stage.stage")
star_module = importlib.import_module("astrbot.core.pipeline.process_stage.method.star_request")
context_module = importlib.import_module("astrbot.core.pipeline.context_utils")

# Load only the compatibility module, not main.py (which registers business
# commands at import time). A distinct package name also avoids discovery order
# coupling to test_commands.py or test_qq_compat.py.
_PACKAGE = "_bare_command_regression_plugin"
if _PACKAGE not in sys.modules:
    package = ModuleType(_PACKAGE)
    package.__path__ = [str(Path(__file__).resolve().parents[1])]
    sys.modules[_PACKAGE] = package
compat_module = importlib.import_module(_PACKAGE + ".qq_compat")
QQMentionCompat = compat_module.QQMentionCompat
is_bare_group_command = compat_module.is_bare_group_command

SENDER = "offline-sender"
GROUP = "offline-group"
MEMBER = "offline-member"
PLUGIN = "offline-plugin"
MODULE = "offline.test_commands"
BUILTIN = "astrbot.builtin_stars.builtin_commands.main"


class _Handlers:
    def __init__(self):
        self.calls = []

    async def silent(self, event):
        self.calls.append(("silent", event, {}))

    async def number(self, event, amount: int):
        self.calls.append(("number", event, {"amount": amount}))

    async def raises(self, event):
        self.calls.append(("raises", event, {}))
        raise ValueError("offline handler failure")

    async def explicit_llm(self, event):
        self.calls.append(("explicit_llm", event, {}))
        yield ProviderRequest(prompt="synthetic explicit request")


class _ProviderSpy:
    def __init__(self):
        self.calls = []

    async def process(self, event):
        self.calls.append(event)
        yield None


class _MemoryRegistry:
    """Fixture for registry selection, with no reads of the process registry."""
    def __init__(self):
        self.handlers = []
        self.queries = []

    def get_handlers_by_event_type(self, event_type, plugins_name=None):
        self.queries.append((event_type, plugins_name))
        if event_type != EventType.AdapterMessageEvent:
            return []
        return [handler for handler in self.handlers
                if handler.enabled and (plugins_name is None
                                        or handler.plugin_name in plugins_name)]


class BareCommandPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Fail even if native code catches a forbidden network exception: teardown
        # asserts that every guard remained unused. No real API client is created.
        guards = [
            (socket.socket, "connect", False),
            (socket.socket, "connect_ex", False),
            (socket.socket, "sendto", False),
            (socket, "create_connection", False),
            (socket, "getaddrinfo", False),
            (httpx.Client, "request", False),
            (httpx.Client, "send", False),
            (httpx.AsyncClient, "request", True),
            (httpx.AsyncClient, "send", True),
            (aiohttp.ClientSession, "_request", True),
        ]
        for owner, name, asynchronous in guards:
            guard = patch.object(
                owner, name, new_callable=AsyncMock if asynchronous else Mock,
                side_effect=AssertionError("Network forbidden in bare-command tests"),
            )
            mock = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(mock.assert_not_called)

        self.registry = _MemoryRegistry()
        self.star_map = {}
        for module in (wake_module, context_module):
            self.enterContext(patch.object(module, "star_handlers_registry", self.registry))
        for module in (wake_module, star_module, context_module):
            self.enterContext(patch.object(module, "star_map", self.star_map))
            self.enterContext(patch.object(module, "logger", Mock()))

        self.blocked_sessions = set()
        self.session_calls = []

        async def session_filter(event, handlers):
            self.session_calls.append((event.session_id, tuple(handlers)))
            return [handler for handler in handlers
                    if (event.session_id, handler.plugin_name) not in self.blocked_sessions]

        self.session_filter = self.enterContext(patch.object(
            wake_module.SessionPluginManager, "filter_handlers_by_session",
            new=AsyncMock(side_effect=session_filter),
        ))
        self.recorder = Mock()
        self.enterContext(patch.object(
            wake_module, "UmoAutoNameRecorder", return_value=self.recorder,
        ))
        self.config = {
            "admins_id": [], "wake_prefix": ["/", "!"],
            "plugin_set": ["*"], "disable_builtin_commands": False,
            "platform_settings": {
                "no_permission_reply": True,
                "friend_message_needs_wake_prefix": True,
                "ignore_bot_self_message": False,
                "ignore_at_all": True,
                "unique_session": False,
            },
            "provider_settings": {"enable": True, "prompt_prefix": "", "identifier": ""},
        }
        self.ctx = SimpleNamespace(
            astrbot_config=self.config, astrbot_config_id="offline",
            plugin_manager=None, db_helper=None,
        )
        self.wake = wake_module.WakingCheckStage()
        await self.wake.initialize(self.ctx)
        self.provider = _ProviderSpy()
        self.process = process_module.ProcessStage()
        # Avoid AgentRequestSubStage.initialize, which owns real provider state.
        # The actual ProcessStage.process and StarRequestSubStage are unmodified.
        self.process.ctx = self.ctx
        self.process.config = self.config
        self.process.plugin_manager = None
        self.process.agent_sub_stage = self.provider
        self.process.star_request_sub_stage = star_module.StarRequestSubStage()
        await self.process.star_request_sub_stage.initialize(self.ctx)
        self.handlers = _Handlers()
        self.compat = QQMentionCompat(Mock())
        self.original_filters = (CommandFilter.filter, CommandGroupFilter.filter)
        self.addCleanup(self.compat.close)
        self.assertTrue(self.compat.install_bare_command_wake())
        self.serial = 0

    def register(self, name="echo", *, method="silent", alias=None,
                 parents=None, filters=(), module=MODULE, plugin=PLUGIN,
                 enabled=True, command_filter=None):
        self.serial += 1
        handler = SimpleNamespace(
            handler=getattr(self.handlers, method),
            handler_name=method,
            handler_full_name=f"{module}.{method}.{self.serial}",
            handler_module_path=module,
            plugin_name=plugin, enabled=enabled, desc="offline command",
        )
        command = command_filter or CommandFilter(name, alias=alias, parent_command_names=parents)
        if isinstance(command, CommandFilter):
            # Native init skips self/event: supply the unbound signature, while
            # the runtime registry stores the bound handler as AstrBot does.
            command.init_handler_md(SimpleNamespace(
                handler=getattr(_Handlers, method), desc="offline command",
            ))
        handler.event_filters = [command, *filters]
        self.registry.handlers.append(handler)
        self.star_map[module] = SimpleNamespace(name=plugin)
        return handler, command

    async def event(self, text="echo", *, sender=SENDER, at_bot=False):
        raw = PatchedGroupMessage(None, "offline-event", {
            "id": "offline-message", "group_openid": GROUP,
            "author": {"member_openid": sender, "username": "offline"},
            "content": text, "attachments": [], "mentions": [],
            "timestamp": "2026-01-01T00:00:00Z",
        })
        message = await QQOfficialPlatformAdapter._parse_from_qqofficial(
            raw, MessageType.GROUP_MESSAGE, force_group_mention=at_bot,
        )
        message.group_id = GROUP
        message.session_id = GROUP
        # Bypass the adapter constructor/login; retain its real event factory.
        platform = object.__new__(QQOfficialPlatformAdapter)
        platform.config = {"id": "offline"}
        platform.client = SimpleNamespace()
        event = platform.create_event(message)

        async def sent(*args, **kwargs):
            event._has_send_oper = True

        self.enterContext(patch.object(event, "send", new=AsyncMock(side_effect=sent)))
        self.assertFalse(event.is_at_or_wake_command)
        self.assertFalse(event.is_admin())
        return event

    async def dispatch(self, event):
        await self.wake.process(event)
        if not event.is_stopped():
            async for _ in self.process.process(event):
                pass
        return event

    def assert_no_llm(self, event):
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(event.is_at_or_wake_command)

    async def test_bare_silent_command_runs_native_handler_without_llm_fallback(self):
        handler, _ = self.register()
        event = await self.dispatch(await self.event())
        self.assertEqual(self.handlers.calls, [("silent", event, {})])
        self.assertEqual(event.get_extra("activated_handlers"), [handler])
        self.assertEqual(event.get_extra("handlers_parsed_params"), {handler.handler_full_name: {}})
        self.assertTrue(event.is_wake)
        self.assertFalse(event._has_send_oper)
        self.assert_no_llm(event)

    async def test_alias_full_subcommand_and_unicode_whitespace_use_native_parser(self):
        handler, _ = self.register("take", method="number", alias={"grab"}, parents=["root", "r"])
        for text in ("root take 7", "r grab 7", "  root\t take\n7  ", "r\u3000grab\u00a07"):
            with self.subTest(text=repr(text)):
                event = await self.dispatch(await self.event(text))
                self.assertEqual(self.handlers.calls[-1], ("number", event, {"amount": 7}))
                self.assertEqual(event.get_extra("handlers_parsed_params")[handler.handler_full_name], {"amount": 7})
                self.assert_no_llm(event)
        self.assertEqual(len(self.handlers.calls), 4)

    async def test_general_chat_and_command_prefix_near_misses_do_not_wake(self):
        self.register()
        for text in ("ordinary chat", "echoes", "echo!", "please echo", "", " \t "):
            with self.subTest(text=text):
                event = await self.dispatch(await self.event(text))
                self.assertTrue(event.is_stopped())
                self.assertEqual(self.handlers.calls, [])
                self.assert_no_llm(event)

    async def test_native_permission_denial_and_admin_role_from_config(self):
        permission = PermissionTypeFilter(PermissionType.ADMIN)
        self.register(filters=[permission])
        denied = await self.dispatch(await self.event())
        self.assertEqual(self.handlers.calls, [])
        denied.send.assert_awaited_once()
        self.assertTrue(denied.is_stopped())
        self.assert_no_llm(denied)
        self.config["admins_id"] = [SENDER]
        admitted = await self.dispatch(await self.event())
        self.assertTrue(admitted.is_admin())
        self.assertEqual(self.handlers.calls, [("silent", admitted, {})])
        self.assert_no_llm(admitted)

    async def test_permission_silent_denial_has_no_handler_or_provider(self):
        self.register(filters=[PermissionTypeFilter(PermissionType.ADMIN, raise_error=False)])
        event = await self.dispatch(await self.event())
        self.assertTrue(event.is_stopped())
        event.send.assert_not_awaited()
        self.assertEqual(self.handlers.calls, [])
        self.assert_no_llm(event)

    async def test_no_permission_reply_setting_is_preserved(self):
        self.wake.no_permission_reply = False
        self.register(filters=[PermissionTypeFilter(PermissionType.ADMIN)])
        event = await self.dispatch(await self.event())
        self.assertTrue(event.is_stopped())
        event.send.assert_not_awaited()
        self.assert_no_llm(event)

    async def test_native_custom_filter_rejection_restores_gate(self):
        _, command = self.register()
        custom = SimpleNamespace(filter=Mock(return_value=False))
        command.add_custom_filter(custom)
        event = await self.dispatch(await self.event())
        custom.filter.assert_called_once_with(event, self.config)
        self.assertEqual(self.handlers.calls, [])
        self.assertTrue(event.is_stopped())
        self.assert_no_llm(event)

    async def test_other_and_filter_observes_restored_flag_and_can_reject(self):
        seen = []
        def reject(event, config):
            seen.append(event.is_at_or_wake_command)
            return False
        self.register(filters=[SimpleNamespace(filter=reject)])
        event = await self.dispatch(await self.event())
        self.assertEqual(seen, [False])
        self.assertEqual(self.handlers.calls, [])
        self.assert_no_llm(event)

    async def test_plugin_set_and_registry_disabled_entries_are_not_dispatched(self):
        self.register()
        self.register("disabled", enabled=False)
        self.config["plugin_set"] = ["another-plugin"]
        event = await self.dispatch(await self.event())
        self.assertIn((EventType.AdapterMessageEvent, ["another-plugin"]), self.registry.queries)
        self.assertEqual(self.handlers.calls, [])
        self.assert_no_llm(event)
        self.config["plugin_set"] = ["*"]
        disabled = await self.dispatch(await self.event("disabled"))
        self.assertEqual(self.handlers.calls, [])
        self.assert_no_llm(disabled)

    async def test_disabled_builtin_is_skipped_by_native_wake_stage(self):
        self.register(module=BUILTIN)
        self.wake.disable_builtin_commands = True
        event = await self.dispatch(await self.event())
        self.assertTrue(event.is_stopped())
        self.assertEqual(self.handlers.calls, [])
        self.assert_no_llm(event)

    async def test_session_removal_after_command_match_does_not_fall_back_to_llm(self):
        handler, _ = self.register()
        self.blocked_sessions.add((GROUP, PLUGIN))
        event = await self.dispatch(await self.event())
        self.assertIn((GROUP, (handler,)), self.session_calls)
        self.assertTrue(event.is_wake)  # Native stage sets this before session filtering.
        self.assertEqual(event.get_extra("activated_handlers"), [])
        self.assertEqual(self.handlers.calls, [])
        self.assert_no_llm(event)

    async def test_unique_session_is_applied_before_session_policy(self):
        self.register()
        self.wake.unique_session = True
        isolated = SENDER + "_" + GROUP
        self.blocked_sessions.add((isolated, PLUGIN))
        event = await self.dispatch(await self.event())
        self.assertEqual(event.session_id, isolated)
        self.assertTrue(event.get_extra("_session_isolated"))
        self.assertEqual(self.handlers.calls, [])
        self.assert_no_llm(event)

    async def test_missing_and_invalid_arguments_use_native_error_send(self):
        self.register("take", method="number")
        for text in ("take", "take not-an-integer"):
            with self.subTest(text=text):
                event = await self.dispatch(await self.event(text))
                event.send.assert_awaited_once()
                self.assertTrue(event.is_stopped())
                self.assertEqual(self.handlers.calls, [])
                self.assert_no_llm(event)

    async def test_handler_exception_keeps_native_stop_and_does_not_start_provider(self):
        self.register(method="raises")
        event = await self.dispatch(await self.event())
        self.assertEqual(self.handlers.calls, [("raises", event, {})])
        self.assertTrue(event.is_stopped())
        # Native exception text is gated by a real mention/prefix; no fake one.
        event.send.assert_not_awaited()
        self.assert_no_llm(event)

    async def test_explicit_plugin_provider_request_is_not_default_fallback(self):
        self.register(method="explicit_llm")
        event = await self.dispatch(await self.event())
        self.assertEqual(self.provider.calls, [event])
        self.assertEqual(event.get_extra("provider_request").prompt, "synthetic explicit request")
        self.assertFalse(event.is_at_or_wake_command)

    async def test_trailing_member_at_is_allowed_and_retained(self):
        self.register("take", method="number")
        event = await self.event("take 7")
        member = At(qq=MEMBER)
        event.message_obj.message.append(member)
        await self.dispatch(event)
        self.assertEqual(self.handlers.calls, [("number", event, {"amount": 7})])
        self.assertIn(member, event.get_messages())
        self.assert_no_llm(event)

    async def test_leading_member_at_atall_and_reply_do_not_gain_bare_gate(self):
        _, command = self.register()
        for segment in (At(qq=MEMBER), AtAll(), Reply(id="offline-reply")):
            with self.subTest(segment=type(segment).__name__):
                event = await self.event()
                event.message_obj.message.insert(0, segment)
                self.assertFalse(is_bare_group_command(event, command))
                await self.dispatch(event)
                self.assertEqual(self.handlers.calls, [])
                self.assert_no_llm(event)

    async def test_real_bot_mention_and_configured_prefixes_keep_native_llm_semantics(self):
        _, command = self.register()
        for text, mention in (("echo", True), ("/echo", False), ("!echo", False)):
            with self.subTest(text=text, mention=mention):
                event = await self.event(text, at_bot=mention)
                if mention:
                    self.assertFalse(is_bare_group_command(event, command))
                await self.dispatch(event)
                self.assertTrue(event.is_at_or_wake_command)
                self.assertEqual(self.handlers.calls[-1], ("silent", event, {}))
                self.assertEqual(self.provider.calls[-1], event)
        self.assertEqual(len(self.provider.calls), 3)

    async def test_unconfigured_slash_is_not_removed_by_compatibility_layer(self):
        self.config["wake_prefix"] = ["!"]
        self.register()
        event = await self.dispatch(await self.event("/echo"))
        self.assertTrue(event.is_stopped())
        self.assertEqual(self.handlers.calls, [])
        self.assert_no_llm(event)

    async def test_scope_rejects_nonqq_webhook_channel_dm_and_mismatched_group(self):
        _, command = self.register()
        for case in ("other", "webhook", "channel", "friend", "empty_group", "mismatch"):
            with self.subTest(case=case):
                event = await self.event()
                if case in ("other", "webhook"):
                    self.enterContext(patch.object(event, "get_platform_name", return_value=(
                        "aiocqhttp" if case == "other" else "qq_official_webhook")))
                elif case == "channel":
                    # A channel can have GROUP_MESSAGE and a group_id; SDK type
                    # must still be verified, not merely duck-typed.
                    event.message_obj.raw_message = SimpleNamespace(group_openid=GROUP)
                elif case == "friend":
                    event.message_obj.type = MessageType.FRIEND_MESSAGE
                elif case == "empty_group":
                    event.message_obj.raw_message.group_openid = ""
                else:
                    event.message_obj.raw_message.group_openid = "different-group"
                self.assertFalse(is_bare_group_command(event, command))
                self.assertFalse(command.filter(event, self.config))
                self.assert_no_llm(event)

    async def test_command_group_help_and_subcommand_keep_native_behavior(self):
        group = CommandGroupFilter("root", alias={"r"})
        self.register(command_filter=group)
        _, leaf = self.register("take", method="number", parents=["root", "r"])
        group.add_sub_command_filter(leaf)
        help_event = await self.dispatch(await self.event("root"))
        help_event.send.assert_awaited_once()
        self.assertTrue(help_event.is_stopped())
        self.assertEqual(self.handlers.calls, [])
        self.assert_no_llm(help_event)
        leaf_event = await self.dispatch(await self.event("r take 7"))
        self.assertEqual(self.handlers.calls, [("number", leaf_event, {"amount": 7})])
        self.assert_no_llm(leaf_event)
        near_miss = await self.dispatch(await self.event("rooted"))
        self.assertTrue(near_miss.is_stopped())
        self.assertEqual(len(self.handlers.calls), 1)
        self.assert_no_llm(near_miss)

    async def test_filter_exception_restores_flag_before_native_error_handler(self):
        _, command = self.register()
        seen = []
        def explode(event, config):
            seen.append(event.is_at_or_wake_command)
            raise ValueError("offline filter failure")
        command.add_custom_filter(SimpleNamespace(filter=explode))
        event = await self.dispatch(await self.event())
        self.assertEqual(seen, [True])
        event.send.assert_awaited_once()
        self.assertTrue(event.is_stopped())
        self.assert_no_llm(event)

    async def test_cancellation_in_sync_filter_also_restores_flag(self):
        _, command = self.register()
        def cancel(event, config):
            raise asyncio.CancelledError()
        command.add_custom_filter(SimpleNamespace(filter=cancel))
        event = await self.event()
        with self.assertRaises(asyncio.CancelledError):
            command.filter(event, self.config)
        self.assert_no_llm(event)

    async def test_two_concurrent_events_keep_params_and_flags_separate(self):
        self.register("take", method="number")
        events = [await self.event("take 7"), await self.event("take 9")]
        await asyncio.gather(*(self.dispatch(event) for event in events))
        self.assertEqual(sorted(call[2]["amount"] for call in self.handlers.calls), [7, 9])
        self.assertEqual({id(call[1]) for call in self.handlers.calls}, {id(event) for event in events})
        for event in events:
            self.assert_no_llm(event)

    async def test_repeated_install_close_restores_both_classes_and_allows_new_owner(self):
        wrappers = (CommandFilter.filter, CommandGroupFilter.filter)
        self.assertFalse(self.compat.install_bare_command_wake())
        self.assertEqual((CommandFilter.filter, CommandGroupFilter.filter), wrappers)
        self.compat.close()
        self.compat.close()
        self.assertEqual((CommandFilter.filter, CommandGroupFilter.filter), self.original_filters)
        self.assertFalse(self.compat.install_bare_command_wake())
        _, command = self.register()
        event = await self.event()
        self.assertFalse(command.filter(event, self.config))
        replacement = QQMentionCompat(Mock())
        self.addCleanup(replacement.close)
        self.assertTrue(replacement.install_bare_command_wake())
        self.assertTrue(command.filter(event, self.config))
        self.assert_no_llm(event)

    async def test_bare_match_logs_are_bounded_and_do_not_include_payload(self):
        _, command = self.register()
        for _ in range(25):
            event = await self.event("echo PRIVATE_BODY_SENTINEL")
            self.assertTrue(command.filter(event, self.config))
            self.assertFalse(event.is_at_or_wake_command)
        logs = [str(call) for call in self.compat.logger.info.call_args_list]
        self.assertEqual(sum("bare_filter_matched=True" in line for line in logs), 20)
        self.assertEqual(self.compat.bare_filter_matches, 25)
        for secret in (SENDER, GROUP, "PRIVATE_BODY_SENTINEL"):
            self.assertNotIn(secret, "\n".join(logs))

    async def test_third_party_wrappers_survive_close_but_owned_layers_become_inert(self):
        _, command = self.register()
        group = CommandGroupFilter("root")
        with patch.object(CommandFilter, "filter", wraps=CommandFilter.filter) as outer_command, \
             patch.object(CommandGroupFilter, "filter", wraps=CommandGroupFilter.filter) as outer_group:
            self.compat.close()
            self.assertIs(CommandFilter.filter, outer_command)
            self.assertIs(CommandGroupFilter.filter, outer_group)
            event = await self.event()
            # Mock descriptors do not bind self; exercise the captured chain explicitly.
            self.assertFalse(outer_command(command, event, self.config))
            self.assertFalse(outer_group(group, await self.event("root take"), self.config))
            self.assert_no_llm(event)
        # patch.object restored our now-inert layers, so clean them explicitly.
        CommandFilter.filter, CommandGroupFilter.filter = self.original_filters


if __name__ == "__main__":
    unittest.main()
