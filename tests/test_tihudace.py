"""Offline tests for 醍醐测智 (codex HTML -> PNG render command)."""
import asyncio
import base64
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from astrbot.api import AstrBotConfig
from astrbot.core.message.components import Image
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

SENDER = "TIHD_SENDER_PRIVATE_SENTINEL"
GROUP = "TIHD_GROUP_PRIVATE_SENTINEL"
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


class ExtractHtmlTests(unittest.TestCase):
    def test_fenced_html(self):
        text = '好的，代码如下：\n```html\n<html><body><svg/></body></html>\n```'
        self.assertEqual(NewApiSuitePlugin._extract_html(text),
                         '<html><body><svg/></body></html>')

    def test_bare_svg(self):
        self.assertEqual(NewApiSuitePlugin._extract_html('<svg width="10"></svg>'),
                         '<svg width="10"></svg>')

    def test_garbage_returns_empty(self):
        self.assertEqual(NewApiSuitePlugin._extract_html('没有代码'), '')


class TihudaceHandlerTests(unittest.IsolatedAsyncioTestCase):
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
        config.update({
            "codex_settings": {"api_key": "sk-test", "model": "gpt-6-astra",
                               "prompt_template": "画一个鹈鹕"},
            "group_whitelist_settings": {"enabled": False},
        })
        self.plugin = object.__new__(NewApiSuitePlugin)
        self.plugin.config = config
        self.plugin.lang = "zh"
        self.plugin._reply = lambda event, text: text
        self.tmpdir = Path("/tmp")  # _render_html_to_png is mocked in most tests
        self.plugin.core = SimpleNamespace(
            codex_chat_completion=AsyncMock(),
            _resolve_plugin_data_dir=lambda: str(self.tmpdir),
        )

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

    async def test_full_flow_acks_then_sends_image(self):
        html = "<html><body><svg/></body></html>"
        self.plugin.core.codex_chat_completion = AsyncMock(
            return_value="```html\n" + html + "\n```")
        fake_png = "/tmp/tihudace.png"
        self.plugin._render_html_to_png = AsyncMock(return_value=fake_png)
        event = await self.event("醍醐测智")
        event.is_at_or_wake_command = True
        command = CommandFilter("醍醐测智")
        command.init_handler_md(SimpleNamespace(handler=NewApiSuitePlugin.handle_tihudace))
        self.assertTrue(command.filter(event, self.plugin.config))
        replies = await self.collect(self.plugin.handle_tihudace(event))
        self.assertIn("正在绘制", replies[0])
        images = [c for c in replies[1].chain if isinstance(c, Image)]
        self.assertEqual(len(images), 1)
        self.plugin.core.codex_chat_completion.assert_awaited_once_with("画一个鹈鹕")

    async def test_model_failure_replies_html_missing(self):
        self.plugin.core.codex_chat_completion = AsyncMock(return_value="抱歉，无法生成。")
        replies = await self.collect(self.plugin.handle_tihudace(await self.event("醍醐测智")))
        self.assertEqual(len(replies), 2)
        self.assertIn("正在绘制", replies[0])
        self.assertIn("没有返回可渲染的 HTML/SVG 内容", replies[1])

    async def test_disabled_without_prompt(self):
        self.plugin.config["codex_settings"]["prompt_template"] = ""
        replies = await self.collect(self.plugin.handle_tihudace(await self.event("醍醐测智")))
        self.assertIn("未配置", replies[0])
        self.plugin.core.codex_chat_completion.assert_not_awaited()

    async def test_render_failure_replies(self):
        self.plugin.core.codex_chat_completion = AsyncMock(return_value="<svg></svg>")
        self.plugin._render_html_to_png = AsyncMock(return_value=None)
        replies = await self.collect(self.plugin.handle_tihudace(await self.event("醍醐测智")))
        self.assertIn("渲染失败", replies[-1])

    async def test_render_html_to_png_uses_chromium_shim(self):
        tmp = Path("/tmp/tihudace_shim_test")
        tmp.mkdir(exist_ok=True)
        shim = tmp / "chromium"
        shim.write_text(
            "#!/bin/sh\n"
            "for a in \"$@\"; do case \"$a\" in --screenshot=*) "
            "out=\"${a#--screenshot=}\";; esac; done\n"
            "python3 -c \"import base64,sys; open(sys.argv[1],'wb').write(base64.b64decode(sys.argv[2]))\" "
            "\"$out\" \"" + base64.b64encode(PNG_1PX).decode() + "\"\n")
        shim.chmod(0o755)
        out_png = str(tmp / "out.png")
        with patch.dict(os.environ, {"PATH": str(tmp) + ":" + os.environ.get("PATH", "")}):
            result = await self.plugin._render_html_to_png("<html></html>", out_png)
        self.assertEqual(result, out_png)
        self.assertTrue(Path(out_png).exists())
        Path(out_png).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
