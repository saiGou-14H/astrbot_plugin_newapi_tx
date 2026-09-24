"""Offline tests for the 中转站指令 help command."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from astrbot.api import AstrBotConfig
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    PatchedGroupMessage, QQOfficialPlatformAdapter,
)
from astrbot.core.star.filter.command import CommandFilter

if "compat_plugin" not in sys.modules:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "compat_plugin", root / "__init__.py", submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot locate compat_plugin")
    sys.modules["compat_plugin"] = importlib.util.module_from_spec(spec)

from compat_plugin.main import NewApiSuitePlugin

SENDER = "HELP_SENDER_PRIVATE_SENTINEL"
GROUP = "HELP_GROUP_PRIVATE_SENTINEL"


class HelpCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        guard = patch.object(
            httpx.AsyncClient, "request", new_callable=AsyncMock,
            side_effect=AssertionError("Real HTTP is forbidden"),
        )
        self.blocked_http = guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.blocked_http.assert_not_awaited)
        self.platform = QQOfficialPlatformAdapter({
            "id": "offline", "appid": "synthetic-app", "secret": "synthetic-secret",
            "enable_group_c2c": True, "enable_guild_direct_message": False,
        }, {}, asyncio.Queue())
        self.addAsyncCleanup(self.platform.client.close)
        config = AstrBotConfig.__new__(AstrBotConfig)
        config.update({})
        self.plugin = object.__new__(NewApiSuitePlugin)
        self.plugin.config = config
        self.plugin.lang = "zh"
        self.plugin._reply = lambda event, text: text

    async def event(self, text):
        raw = PatchedGroupMessage(None, "offline-event", {
            "id": "offline-message", "group_openid": GROUP,
            "author": {"member_openid": SENDER, "username": "offline"},
            "content": text, "attachments": [], "mentions": [],
            "timestamp": "2026-01-01T00:00:00Z",
        })
        message = await QQOfficialPlatformAdapter._parse_from_qqofficial(
            raw, MessageType.GROUP_MESSAGE)
        message.group_id = GROUP
        message.session_id = GROUP
        return self.platform.create_event(message)

    async def test_help_lists_all_command_families(self):
        event = await self.event("中转站指令")
        event.is_at_or_wake_command = True
        command = CommandFilter("中转站指令")
        command.init_handler_md(SimpleNamespace(handler=NewApiSuitePlugin.handle_tx_help))
        self.assertTrue(command.filter(event, self.plugin.config))
        replies = [item async for item in self.plugin.handle_tx_help(event)]
        text = replies[0]
        for phrase in ("中转站指令大全", "绑定 网站ID", "签到", "打劫", "PK 网站ID",
                       "接受PK", "抢红包", "个人红包", "调整余额", "查ID", "new-tx",
                       "pingapi", "提及诊断", "模型情况"):
            self.assertIn(phrase, text)

    async def test_help_alias_and_english(self):
        for name in ("指令大全", "中转站帮助"):
            event = await self.event(name)
            event.is_at_or_wake_command = True
            command = CommandFilter("中转站指令", alias={"中转站帮助", "指令大全"})
            command.init_handler_md(SimpleNamespace(handler=NewApiSuitePlugin.handle_tx_help))
            self.assertTrue(command.filter(event, self.plugin.config))
        self.plugin.lang = "en"
        replies = [item async for item in self.plugin.handle_tx_help(await self.event("中转站指令"))]
        self.assertIn("Command Reference", replies[0])
        self.assertIn("PK <website ID>", replies[0])


if __name__ == "__main__":
    unittest.main()
