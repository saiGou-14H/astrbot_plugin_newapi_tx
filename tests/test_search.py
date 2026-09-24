"""Offline tests for 查ID (search NewAPI users by username/email)."""
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

from compat_plugin.newapi_utils import NewApiCore
from compat_plugin.main import NewApiSuitePlugin

SENDER = "SEARCH_SENDER_PRIVATE_SENTINEL"
GROUP = "SEARCH_GROUP_PRIVATE_SENTINEL"


class SearchApiUsersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config = AstrBotConfig.__new__(AstrBotConfig)
        config.update({"binding_settings": {"quota_display_ratio": 100}})
        self.core = NewApiCore(config)
        self.core.api_request = AsyncMock()

    async def test_keyword_matches_username_and_email_with_dedup(self):
        self.core.api_request.return_value = {"success": True, "data": [
            {"id": 1, "username": "zhangsan", "email": "a@example.com"},
            {"id": 2, "username": "lisi", "email": "zhang@example.com"},
            {"id": 1, "username": "zhangsan", "email": "a@example.com"},
            {"id": 3, "username": "wangwu", "email": "other@example.com"},
        ]}
        users = await self.core.search_api_users("zhang")
        self.assertEqual([u["user_id"] for u in users], [1, 2])

    async def test_falls_back_to_username_and_email_endpoints(self):
        self.core.api_request.side_effect = [
            {"success": False},
            {"success": True, "data": {"items": [
                {"id": 9, "username": "target", "email": "x@example.com"}]}},
        ]
        users = await self.core.search_api_users("target")
        self.assertEqual(users[0]["user_id"], 9)
        # search → username → email：三个端点都会被尝试（后两个因 side_effect 耗尽被吞掉）
        self.assertEqual(self.core.api_request.await_count, 3)

    async def test_limit_and_no_match_and_empty_keyword(self):
        self.core.api_request.return_value = {"success": True, "data": [
            {"id": i, "username": f"user{i}", "email": ""} for i in range(1, 10)]}
        users = await self.core.search_api_users("user")
        self.assertEqual(len(users), 5)
        self.core.api_request.return_value = {"success": True, "data": []}
        self.assertIsNone(await self.core.search_api_users("nobody"))
        self.assertIsNone(await self.core.search_api_users("   "))

    async def test_api_failure_returns_none(self):
        self.core.api_request.side_effect = [{"success": False}, {"success": False}, {"success": False}]
        self.assertIsNone(await self.core.search_api_users("zhang"))


class SearchCommandHandlerTests(unittest.IsolatedAsyncioTestCase):
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

    async def collect(self, generator):
        return [item async for item in generator]

    async def test_search_prints_masked_email_and_usage(self):
        event = await self.event("查ID zhangsan")
        event.is_at_or_wake_command = True
        command = CommandFilter("查ID", alias={"查用户", "用户ID"})
        command.init_handler_md(SimpleNamespace(handler=NewApiSuitePlugin.handle_search_user))
        self.assertTrue(command.filter(event, self.plugin.config))
        self.plugin.core = SimpleNamespace(search_api_users=AsyncMock(return_value=[
            {"user_id": 13, "username": "zhangsan", "email": "zhangsan@example.com"},
        ]))
        params = event.get_extra("parsed_params")
        replies = await self.collect(self.plugin.handle_search_user(event, **params))
        text = replies[0]
        self.assertIn("ID: 13", text)
        self.assertIn("用户名: zhangsan", text)
        self.assertIn("邮箱: z***@example.com", text)
        self.assertNotIn("zhangsan@example.com", text)
        usage_event = await self.event("查ID")
        self.plugin.core.search_api_users = AsyncMock()
        replies = await self.collect(self.plugin.handle_search_user(usage_event, ""))
        self.assertIn("用法", replies[0])
        self.plugin.core.search_api_users.assert_not_awaited()

    async def test_not_found_and_failed_replies(self):
        event = await self.event("查ID nobody")
        self.plugin.core = SimpleNamespace(
            search_api_users=AsyncMock(return_value=None))
        replies = await self.collect(self.plugin.handle_search_user(event, "nobody"))
        self.assertIn("接口调用失败", replies[0])
        self.plugin.core.search_api_users = AsyncMock(return_value=[])
        replies = await self.collect(self.plugin.handle_search_user(event, "nobody"))
        self.assertIn("没有找到", replies[0])


if __name__ == "__main__":
    unittest.main()
