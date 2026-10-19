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

from compat_plugin.main import NewApiSuitePlugin, TihudaceScheduler

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

    def test_any_language_fence_and_doctype(self):
        self.assertEqual(
            NewApiSuitePlugin._extract_html('```xml\n<svg width="1"/>\n```'),
            '<svg width="1"/>')
        self.assertEqual(
            NewApiSuitePlugin._extract_html('<!DOCTYPE html><html></html>'),
            '<!DOCTYPE html><html></html>')


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

    async def test_working_message_uses_configured_model(self):
        self.plugin.config['codex_settings']['model'] = 'gpt-6.1-sol'
        self.plugin.core.codex_chat_completion = AsyncMock(return_value='<svg/>')
        self.plugin._render_html_to_png = AsyncMock(return_value='/tmp/tihudace.png')
        replies = await self.collect(self.plugin.handle_tihudace(await self.event('醍醐测智')))
        self.assertIn('已召唤 gpt-6.1-sol', replies[0])
        self.assertNotIn('gpt-6-astra', replies[0])

    async def test_concurrent_generation_uses_distinct_files(self):
        self.plugin.core.codex_chat_completion = AsyncMock(return_value='<svg/>')
        self.plugin._render_html_to_png = AsyncMock(side_effect=lambda html, path: path)
        results = await asyncio.gather(self.plugin._generate_tihudace_png(),
                                       self.plugin._generate_tihudace_png())
        self.assertEqual([r[0] for r in results], ['OK', 'OK'])
        self.assertNotEqual(results[0][1], results[1][1])

    async def test_model_failure_replies_html_missing(self):
        self.plugin.core.codex_chat_completion = AsyncMock(return_value="抱歉，无法生成。")
        replies = await self.collect(self.plugin.handle_tihudace(await self.event("醍醐测智")))
        self.assertEqual(len(replies), 2)
        self.assertIn("正在绘制", replies[0])
        self.assertIn("没有返回可渲染的 HTML/SVG 内容", replies[1])

    async def test_model_request_failures_are_not_reported_as_bad_html(self):
        from compat_plugin.newapi_utils import CodexGenerationError
        for code, phrase in [('TIMEOUT', '模型生成超时'), ('HTTP_ERROR', '模型请求失败')]:
            with self.subTest(code=code):
                self.plugin.core.codex_chat_completion = AsyncMock(side_effect=CodexGenerationError(code))
                self.plugin._render_html_to_png = AsyncMock()
                replies = await self.collect(self.plugin.handle_tihudace(await self.event('醍醐测智')))
                self.assertIn(phrase, replies[-1])
                self.assertNotIn('没有返回可渲染', replies[-1])
                self.plugin._render_html_to_png.assert_not_awaited()

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


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        guard = patch.object(
            httpx.AsyncClient, "request", new_callable=AsyncMock,
            side_effect=AssertionError("Real HTTP is forbidden"),
        )
        self.blocked_http = guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.blocked_http.assert_not_awaited)
        self.jobs = {}
        self.cron = SimpleNamespace(
            add_basic_job=AsyncMock(side_effect=self._add_job),
            delete_job=AsyncMock(side_effect=self._delete_job),
        )
        self.qq_platform = SimpleNamespace(meta=lambda: SimpleNamespace(id='offline-onebot', name='aiocqhttp'))
        self.context = SimpleNamespace(
            cron_manager=self.cron, send_message=AsyncMock(return_value=True),
            platform_manager=SimpleNamespace(platform_insts=[self.qq_platform]),
            get_platform_inst=lambda pid: self.qq_platform if pid == 'offline-onebot' else None,
        )
        config = AstrBotConfig.__new__(AstrBotConfig)
        config.update({
            "codex_settings": {"prompt_template": "画一个鹈鹕"},
            "tihudace_settings": {
                "scheduled_enabled": True, "interval_minutes": 60,
                "push_targets": [{"name": "g", "group_id": "123", "enabled": True}],
                "dingtalk_enabled": True, "dingtalk_webhook": "https://oapi.dingtalk.com/robot/send?access_token=x",
            },
        })
        self.plugin = object.__new__(NewApiSuitePlugin)
        self.plugin.config = config
        self.plugin.get_kv_data = AsyncMock(return_value={})
        self.plugin._generate_tihudace_png = AsyncMock(return_value=("OK", "/tmp/x.png"))

    async def _add_job(self, **kwargs):
        job = SimpleNamespace(job_id="job-1", **kwargs)
        self.jobs["job-1"] = kwargs
        return job

    async def _delete_job(self, job_id):
        self.jobs.pop(job_id, None)

    async def test_interval_to_cron(self):
        self.assertEqual(TihudaceScheduler._interval_to_cron(15), "*/15 * * * *")
        self.assertEqual(TihudaceScheduler._interval_to_cron(360), "0 */6 * * *")
        with self.assertRaises(ValueError):
            TihudaceScheduler._interval_to_cron(45)

    async def test_initialize_registers_and_terminate_deletes(self):
        sched = TihudaceScheduler(self.plugin, self.context)
        await sched.initialize()
        self.assertIn("job-1", self.jobs)
        self.assertEqual(self.jobs["job-1"]["cron_expression"], "0 */1 * * *")
        await sched.terminate()
        self.assertNotIn("job-1", self.jobs)

    async def test_initialize_cleans_stale_jobs(self):
        stale = SimpleNamespace(job_id="stale-1",
                                payload={"plugin_job_key": TihudaceScheduler.PLUGIN_JOB_KEY})
        keep = SimpleNamespace(job_id="keep-1", payload={"plugin_job_key": "other"})
        self.cron.list_jobs = AsyncMock(return_value=[stale, keep])
        sched = TihudaceScheduler(self.plugin, self.context)
        await sched.initialize()
        self.assertEqual(self.cron.delete_job.call_args_list[0].args[0], "stale-1")

    async def test_run_job_pushes_qq_and_dingtalk(self):
        sched = TihudaceScheduler(self.plugin, self.context)
        post = AsyncMock(return_value=SimpleNamespace(
            status_code=200, json=lambda: {"errcode": 0}))
        with patch("httpx.AsyncClient") as factory:
            factory.return_value.__aenter__.return_value.post = post
            await sched.run_push_job()
        self.context.send_message.assert_awaited_once()
        post.assert_awaited_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://oapi.dingtalk.com/robot/send?access_token=x")
        self.assertEqual(kwargs["json"]["msgtype"], "markdown")

    async def test_qq_timestamp_is_beijing_completion_time_shared_by_all_groups(self):
        from datetime import datetime, timedelta, timezone
        self.plugin.config['tihudace_settings']['push_targets'] = ['123', '456']
        self.plugin.config['codex_settings']['model'] = 'gpt-6.1-sol'
        # UTC date differs from Beijing date; do not depend on container TZ.
        instant = datetime(2026, 9, 29, 16, 1, 2, tzinfo=timezone.utc)
        scheduler = TihudaceScheduler(self.plugin, self.context)
        scheduler._send_qq_group = AsyncMock(return_value=True)
        scheduler._send_dingtalk = AsyncMock(return_value=True)
        with patch('compat_plugin.main.datetime') as clock:
            clock.now.side_effect = lambda tz: instant.astimezone(tz)
            async def generate():
                clock.now.assert_not_called()
                return 'OK', '/tmp/x.png'
            self.plugin._generate_tihudace_png.side_effect = generate
            await scheduler.run_push_job()
        clock.now.assert_called_once_with(timezone(timedelta(hours=8)))
        self.assertEqual(scheduler._send_qq_group.await_count, 2)
        captions = [call.args[1] for call in scheduler._send_qq_group.await_args_list]
        self.assertEqual(captions[0], captions[1])
        self.assertEqual(captions[0], '醍醐测智 | gpt-6.1-sol 定时绘制完成\n'
                         '生成时间：2026-09-30 00:01:02（北京时间 UTC+8）')
        self.assertEqual(scheduler._send_dingtalk.await_args.args[1],
                         '醍醐测智 | gpt-6.1-sol 定时绘制完成')

    async def test_signed_dingtalk_sender_uses_configured_secret(self):
        from urllib.parse import parse_qs, urlsplit
        self.plugin.config['tihudace_settings']['dingtalk_secret'] = 'SEC-test-signing-only'
        post = AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda: {'errcode': 0}))
        with patch('httpx.AsyncClient') as factory, patch(
                'compat_plugin.dingtalk_signing.time.time_ns', return_value=1600000000123000000):
            factory.return_value.__aenter__.return_value.post = post
            sent = await TihudaceScheduler(self.plugin, self.context)._send_dingtalk(
                self.plugin.config['tihudace_settings']['dingtalk_webhook'], 'test', '/tmp/x.png')
        self.assertTrue(sent)
        query = parse_qs(urlsplit(post.call_args.args[0]).query)
        self.assertEqual(query['timestamp'], ['1600000000123'])
        self.assertEqual(query['sign'], ['yN/EMb8xl0s8mZUj5Czzp6WaLGibfvn7EK/RdBUjp2s='])
        self.assertNotIn('图片已推送', post.call_args.kwargs['json']['markdown']['text'])

    async def test_http_200_business_error_is_not_delivery_success(self):
        post = AsyncMock(return_value=SimpleNamespace(
            status_code=200, json=lambda: {'errcode': 310000, 'errmsg': 'not logged'}))
        with patch('httpx.AsyncClient') as factory, patch('compat_plugin.main.logger') as log:
            factory.return_value.__aenter__.return_value.post = post
            sent = await TihudaceScheduler(self.plugin, self.context)._send_dingtalk(
                self.plugin.config['tihudace_settings']['dingtalk_webhook'], 'test', '/tmp/x.png')
        self.assertFalse(sent)
        log.info.assert_not_called()
        self.assertIn('errcode=310000', str(log.warning.call_args))
        self.assertNotIn('not logged', str(log.mock_calls))

    async def test_network_errors_do_not_leak_webhook_or_secret(self):
        self.plugin.config['tihudace_settings']['dingtalk_secret'] = 'SEC-private-sentinel'
        webhook = 'https://oapi.dingtalk.com/robot/send?access_token=private-token-sentinel'
        post = AsyncMock(side_effect=httpx.ConnectError(webhook + ' SEC-private-sentinel'))
        with patch('httpx.AsyncClient') as factory, patch('compat_plugin.main.logger') as log:
            factory.return_value.__aenter__.return_value.post = post
            sent = await TihudaceScheduler(self.plugin, self.context)._send_dingtalk(webhook, 'test', '')
        self.assertFalse(sent)
        output = str(log.mock_calls)
        self.assertNotIn('private-token-sentinel', output)
        self.assertNotIn('SEC-private-sentinel', output)

    async def test_dingtalk_embeds_uploaded_image_and_signs_after_upload(self):
        storage = {'enabled': True, 'access_key': 'key-private', 'secret_key': 'secret-private'}
        self.plugin.config['image_storage_settings'] = storage
        url = 'https://oss.example.com/screens/reports/unique.png'
        post = AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda: {'errcode': 0}))
        order = []
        async def upload(path, conf):
            self.assertEqual(path, '/tmp/x.png')
            self.assertEqual(conf, storage)
            order.append('upload')
            return url
        def sign(webhook, secret):
            order.append('sign')
            return webhook
        with patch('compat_plugin.image_storage.upload_tihudace_png', side_effect=upload), patch(
                'compat_plugin.dingtalk_signing.signed_webhook_url', side_effect=sign), patch(
                'httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value.post = post
            sent = await TihudaceScheduler(self.plugin, self.context)._send_dingtalk(
                self.plugin.config['tihudace_settings']['dingtalk_webhook'], 'test', '/tmp/x.png')
        self.assertTrue(sent)
        self.assertEqual(order, ['upload', 'sign'])
        text = post.call_args.kwargs['json']['markdown']['text']
        self.assertIn(f'![醍醐测智生成图片]({url})', text)
        self.assertIn(f'[查看原图]({url})', text)
        for private in ('key-private', 'secret-private', '/tmp/x.png'):
            self.assertNotIn(private, text)

    async def test_upload_failure_reports_text_fallback_without_image_success(self):
        self.plugin.config['image_storage_settings'] = {'enabled': True}
        post = AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda: {'errcode': 0}))
        with patch('compat_plugin.image_storage.upload_tihudace_png', new_callable=AsyncMock,
                   side_effect=RuntimeError('SECRET-sentinel')) as upload, patch(
                'httpx.AsyncClient') as factory, patch('compat_plugin.main.logger') as log:
            factory.return_value.__aenter__.return_value.post = post
            sched = TihudaceScheduler(self.plugin, self.context)
            sent = await sched._send_dingtalk(
                self.plugin.config['tihudace_settings']['dingtalk_webhook'], 'test', '/tmp/x.png')
        self.assertFalse(sent)
        upload.assert_awaited_once()
        text = post.call_args.kwargs['json']['markdown']['text']
        self.assertIn('图片上传或公开访问校验失败', text)
        self.assertNotIn('![', text)
        self.assertNotIn('SECRET-sentinel', str(log.mock_calls) + text)
        log.info.assert_not_called()

    async def test_storage_diagnostics_are_allowlisted_and_full_has_actionable_fallback(self):
        from compat_plugin.image_storage import ImageStorageError, SAFE_ERROR_CODES
        private = 'SECRET-token-url-resource-sentinel'
        cases = [(ImageStorageError(code), code) for code in SAFE_ERROR_CODES]
        cases += [(ImageStorageError(private), 'upload_failed'),
                  (ImageStorageError(['storage_full', private]), 'upload_failed'),
                  (RuntimeError(private), 'upload_failed')]
        self.plugin.config['image_storage_settings'] = {'enabled': True}
        for error, expected in cases:
            with self.subTest(expected=expected):
                # Exception messages and attached fields must never reach logs/payloads.
                error.args = (private,)
                error.resource = private
                post = AsyncMock(return_value=SimpleNamespace(
                    status_code=200, json=lambda: {'errcode': 0}))
                with patch('compat_plugin.image_storage.upload_tihudace_png',
                           new_callable=AsyncMock, side_effect=error) as upload, patch(
                        'httpx.AsyncClient') as factory, patch('compat_plugin.main.logger') as log:
                    factory.return_value.__aenter__.return_value.post = post
                    sent = await TihudaceScheduler(self.plugin, self.context)._send_dingtalk(
                        self.plugin.config['tihudace_settings']['dingtalk_webhook'],
                        'test', '/tmp/x.png')
                self.assertFalse(sent)
                upload.assert_awaited_once()
                post.assert_awaited_once()
                log.info.assert_not_called()
                text = post.call_args.kwargs['json']['markdown']['text']
                output = str(log.mock_calls)
                self.assertIn('code=' + expected, output)
                self.assertNotIn(private, output + text)
                self.assertNotIn('![', text)
                self.assertIn('本次仅发送文字摘要', text)
                if expected == 'storage_full':
                    self.assertIn('存储空间不足', text)
                    self.assertIn('清理或扩容', text)
                else:
                    self.assertNotIn('存储空间不足', text)

    async def test_disabled_storage_sends_text_without_upload(self):
        post = AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda: {'errcode': 0}))
        with patch('compat_plugin.image_storage.upload_tihudace_png', new_callable=AsyncMock) as upload, patch(
                'httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value.post = post
            await TihudaceScheduler(self.plugin, self.context).run_push_job()
        upload.assert_not_awaited()
        self.context.send_message.assert_awaited_once()
        self.assertIn('未启用图片存储', post.call_args.kwargs['json']['markdown']['text'])

    async def test_qq_still_sends_when_image_upload_fails(self):
        self.plugin.config['image_storage_settings'] = {'enabled': True}
        with patch('compat_plugin.image_storage.upload_tihudace_png', new_callable=AsyncMock,
                   side_effect=RuntimeError('upload unavailable')), patch('httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=SimpleNamespace(status_code=200, json=lambda: {'errcode': 0}))
            await TihudaceScheduler(self.plugin, self.context).run_push_job()
        self.context.send_message.assert_awaited_once()

    async def test_multiple_targets_deduplicate_and_isolate_failures(self):
        self.plugin.config['tihudace_settings']['push_targets'] = [
            '123', '456', '123', None, {'enabled': False, 'group_id': '789'}]
        post = AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda: {'errcode': 0}))
        with patch('compat_plugin.qq_push_sender.send_qq_group_image', new_callable=AsyncMock,
                   side_effect=[RuntimeError('private-error'), True]) as send, patch('httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value.post = post
            await TihudaceScheduler(self.plugin, self.context).run_push_job()
        self.assertEqual(send.await_count, 2)
        self.assertEqual([c.args[1]['group_id'] for c in send.await_args_list], ['123', '456'])
        self.assertEqual([c.args[1]['platform_id'] for c in send.await_args_list], ['offline-onebot'] * 2)
        post.assert_awaited_once()

    async def test_unbound_official_number_skips_qq_but_keeps_dingtalk(self):
        self.context.platform_manager.platform_insts = [SimpleNamespace(
            meta=lambda: SimpleNamespace(id='actual-bot-id', name='qq_official'))]
        self.plugin.config['tihudace_settings']['push_targets'] = ['942692109']
        post = AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda: {'errcode': 0}))
        with patch('compat_plugin.qq_push_sender.send_qq_group_image', new_callable=AsyncMock) as send, patch(
                'httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value.post = post
            await TihudaceScheduler(self.plugin, self.context).run_push_job()
        send.assert_not_awaited()
        post.assert_awaited_once()

    async def test_bindings_alone_do_not_subscribe_and_empty_list_stops_qq(self):
        self.plugin.config['tihudace_settings']['push_targets'] = []
        self.plugin.get_kv_data.return_value = {'123': {
            'platform_id': 'offline-onebot', 'platform_type': 'aiocqhttp', 'group_id': '123'}}
        with patch('compat_plugin.qq_push_sender.send_qq_group_image', new_callable=AsyncMock) as send, patch(
                'httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=SimpleNamespace(status_code=200, json=lambda: {'errcode': 0}))
            await TihudaceScheduler(self.plugin, self.context).run_push_job()
        send.assert_not_awaited()

    async def test_timeout_releases_lock_and_next_scheduled_run_can_succeed(self):
        sched = TihudaceScheduler(self.plugin, self.context)
        sched._send_qq_group = AsyncMock(return_value=True)
        sched._send_dingtalk = AsyncMock(return_value=True)
        self.plugin._generate_tihudace_png.side_effect = [('MODEL_TIMEOUT', None), ('OK', '/tmp/x.png')]
        await sched.run_push_job()
        self.assertEqual(sched.run_status()[0], 'MODEL_TIMEOUT')
        self.assertFalse(sched._run_lock.locked())
        sched._send_qq_group.assert_not_awaited()
        sched._send_dingtalk.assert_not_awaited()
        await sched.run_push_job()
        self.assertEqual(sched.run_status()[0], 'OK')
        sched._send_qq_group.assert_awaited_once()
        sched._send_dingtalk.assert_awaited_once()

    async def test_running_status_no_overlap_and_cancellation_releases_lock(self):
        sched = TihudaceScheduler(self.plugin, self.context)
        entered = asyncio.Event()
        release = asyncio.Event()
        async def generate():
            entered.set()
            await release.wait()
            return 'OK', '/tmp/x.png'
        self.plugin._generate_tihudace_png.side_effect = generate
        task = asyncio.create_task(sched.run_push_job())
        await entered.wait()
        self.assertEqual(sched.run_status()[0], 'GENERATING')
        await sched.run_push_job()
        self.assertEqual(self.plugin._generate_tihudace_png.await_count, 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(sched.run_status()[0], 'CANCELLED')
        self.assertFalse(sched._run_lock.locked())

    async def test_unexpected_generation_error_records_failure_without_stuck_lock(self):
        sched = TihudaceScheduler(self.plugin, self.context)
        self.plugin._generate_tihudace_png.side_effect = RuntimeError('secret-private-sentinel')
        with patch('compat_plugin.main.logger') as log:
            await sched.run_push_job()
        self.assertEqual(sched.run_status()[0], 'INTERNAL_ERROR')
        self.assertFalse(sched._run_lock.locked())
        self.assertNotIn('secret-private-sentinel', str(log.mock_calls))

    async def test_run_job_skips_when_generation_fails(self):
        self.plugin._generate_tihudace_png = AsyncMock(return_value=("HTML_MISSING", None))
        sched = TihudaceScheduler(self.plugin, self.context)
        await sched.run_push_job()
        self.context.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
