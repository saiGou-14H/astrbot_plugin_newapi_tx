"""主流程集成测试：真实导入 main.py（需要 astrbot 运行时，宿主缺少时跳过）。

只替换 I/O 边界（模型请求、Chromium 渲染、事件对象），
验证命令与定时推送实际使用的编排逻辑。
"""

import asyncio
import os
import sys
import types

import pytest

pytest.importorskip('astrbot', reason='需要 AstrBot 运行时（在容器内执行）')

from i18n import translate  # noqa: E402
from tihudace_batch import TargetOutcome  # noqa: E402
from tihudace_targets import TihudaceTarget  # noqa: E402

# main.py 使用包内相对导入，必须按 AstrBot 的方式以包成员加载。
# tests/ -> 插件目录 -> plugins/
_PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)
plugin_main = pytest.importorskip(
    'astrbot_plugin_newapi_tx.main',
    reason='需要把插件按包加载（在容器内执行）',
)


def build_plugin(config, *, token='sk-shared', contents=None):
    """构造未初始化 __init__ 的插件实例，只填测试需要的协作对象。"""
    plugin = object.__new__(plugin_main.NewApiSuitePlugin)
    plugin.config = config
    plugin.lang = 'zh'
    plugin.core = types.SimpleNamespace(api_access_token=token)
    state = {'contents': dict(contents or {}), 'calls': []}

    async def fake_completion(prompt, target=None):
        state['calls'].append((target.model if target else None, prompt))
        value = state['contents'].get(target.model if target else 'default')
        if isinstance(value, Exception):
            raise value
        return value

    async def fake_render_html_to_png(html, png_path):
        with open(png_path, 'w', encoding='utf-8') as handle:
            handle.write(html)
        return png_path

    plugin.core.codex_chat_completion = fake_completion
    plugin._render_html_to_png = fake_render_html_to_png
    plugin._state = state
    return plugin


def patch_data_dir(plugin, tmp_path):
    plugin.core._resolve_plugin_data_dir = lambda: str(tmp_path)


CONFIG = {
    'codex_settings': {
        'api_key': '',
        'prompt_template': '画一个 SVG',
        'targets': [
            'A站|https://a.example.com|sk-a|model-a',
            'https://b.example.com|sk-b|model-b',
            'https://c.example.com|sk-c|model-c',
        ],
    },
}


def test_targets_resolved_from_config_with_shared_key_fallback():
    plugin = build_plugin({
        'codex_settings': {'api_key': '', 'targets': ['https://a.example.com||model-a']},
    }, token='sk-from-api')
    targets, issues = plugin._tihudace_targets()
    assert issues == []
    assert [t.model for t in targets] == ['model-a']
    assert targets[0].api_key == 'sk-from-api'


def test_targets_empty_means_legacy_single_site():
    plugin = build_plugin({'codex_settings': {'prompt_template': 'x', 'targets': []}})
    targets, issues = plugin._tihudace_targets()
    assert (targets, issues) == ([], [])


def test_bad_rows_are_reported_and_good_rows_kept():
    plugin = build_plugin({
        'codex_settings': {'targets': [
            'https://ok.example.com|sk-ok|m1',
            'broken-row-with-secret|sk-leak',
        ]},
    })
    targets, issues = plugin._tihudace_targets()
    assert [t.model for t in targets] == ['m1']
    assert issues and issues[0].index == 2
    text = plugin._tihudace_issue_text(issues)
    assert 'sk-leak' not in text
    assert '第 2 条' in text


def test_batch_generation_is_isolated_per_site(tmp_path):
    from astrbot_plugin_newapi_tx.newapi_utils import CodexGenerationError

    plugin = build_plugin(
        CONFIG,
        contents={
            'model-a': '```html\n<svg><circle r="5"/></svg>\n```',
            'model-b': CodexGenerationError('TIMEOUT'),
            'model-c': '这不是 HTML',
        },
    )
    patch_data_dir(plugin, tmp_path)
    targets, _ = plugin._tihudace_targets()

    async def run():
        return await plugin._generate_tihudace_batch(targets)

    outcomes, prompt = asyncio.run(run())
    assert prompt == '画一个 SVG'
    assert [(o.target.model, o.status) for o in outcomes] == [
        ('model-a', 'OK'), ('model-b', 'MODEL_TIMEOUT'), ('model-c', 'HTML_MISSING')]
    assert outcomes[0].png and os.path.exists(outcomes[0].png)
    assert outcomes[1].png == '' and outcomes[2].png == ''
    # 每个站点都收到了同一提示词，且用的是各自的模型
    assert [call[0] for call in plugin._state['calls']] == ['model-a', 'model-b', 'model-c']


def test_batch_report_lists_every_site_and_counts(tmp_path):
    plugin = build_plugin(CONFIG)
    outcomes = [
        TargetOutcome(TihudaceTarget('A站', 'https://a', 'k', 'model-a'), 'OK', '/tmp/a.png'),
        TargetOutcome(TihudaceTarget('', 'https://b.example.com', 'k', 'model-b'), 'MODEL_FAILED'),
    ]
    text = plugin._tihudace_batch_report(outcomes)
    assert '1/2' in text
    assert 'A站' in text and 'model-a' in text
    assert 'model-b@b.example.com' in text
    assert 'model-b' in text
    # 成功站点必须说“已出图”，不能复用推送阶段/异常文案。
    ok_line = [line for line in text.splitlines() if 'A站' in line][0]
    assert '已出图' in ok_line
    assert '异常' not in ok_line
    assert '推送' not in ok_line


def test_batch_report_all_failed_has_hint(tmp_path):
    plugin = build_plugin(CONFIG)
    outcomes = [TargetOutcome(TihudaceTarget('A', 'https://a', 'k', 'm'), 'RENDER_FAILED')]
    text = plugin._tihudace_batch_report(outcomes)
    assert '0/1' in text
    assert '没有站点成功出图' in text


def test_render_status_mapping(tmp_path):
    plugin = build_plugin(CONFIG)
    patch_data_dir(plugin, tmp_path)

    async def run(content):
        return await plugin._tihudace_render(content)

    assert asyncio.run(run('')) == ('MODEL_FAILED', '')
    assert asyncio.run(run('纯文本，没有标签')) == ('HTML_MISSING', '')
    status, png = asyncio.run(run('<html><body>hi</body></html>'))
    assert status == 'OK' and os.path.exists(png)


def test_english_locale_batch_report(tmp_path):
    plugin = build_plugin(CONFIG)
    plugin.lang = 'en'
    outcomes = [TargetOutcome(TihudaceTarget('A', 'https://a', 'k', 'model-a'), 'OK', '/tmp/a.png')]
    text = plugin._tihudace_batch_report(outcomes)
    assert 'Batch results' in text
    assert '1/1' in text


def test_scheduler_collect_images_uses_batch_when_targets_present(tmp_path):
    plugin = build_plugin(
        dict(CONFIG, tihudace_settings={}),
        contents={'model-a': '<svg/>', 'model-b': '<svg/>', 'model-c': '<svg/>'},
    )
    patch_data_dir(plugin, tmp_path)
    scheduler = plugin_main.TihudaceScheduler(plugin, context=None)
    images, failure = asyncio.run(scheduler._collect_images())
    assert failure == 'MODEL_FAILED'
    assert len(images) == 3
    assert {image[2] for image in images} == {'A站', 'model-b@b.example.com', 'model-c@c.example.com'}


def test_scheduler_single_site_fallback_keeps_legacy_summary(tmp_path):
    plugin = build_plugin({
        'codex_settings': {'prompt_template': '画图', 'model': 'solo-model'},
    })
    plugin.core.codex_chat_completion = lambda prompt, target=None: _await('<svg/>')
    plugin._render_html_to_png = _fake_render
    patch_data_dir(plugin, tmp_path)
    scheduler = plugin_main.TihudaceScheduler(plugin, context=None)
    images, failure = asyncio.run(scheduler._collect_images())
    assert failure == 'MODEL_FAILED'
    assert images[0][0] == 'solo-model'
    assert images[0][2] == ''


async def _await(value):
    return value


async def _fake_render(html, png_path):
    with open(png_path, 'w', encoding='utf-8') as handle:
        handle.write(html)
    return png_path
