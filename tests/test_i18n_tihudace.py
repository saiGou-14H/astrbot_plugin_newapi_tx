"""i18n 与帮助文案测试：新增功能必须同步中英文帮助与文案键。"""

import re

import pytest

import i18n
from i18n import _TRANSLATIONS, translate

LANGS = ('zh', 'en')


def keys_matching(pattern):
    pattern = re.compile(pattern)
    return {
        lang: {key for key in _TRANSLATIONS[lang] if pattern.match(key)}
        for lang in LANGS
    }


def test_help_body_documents_multi_site_batch_in_both_languages():
    zh = _TRANSLATIONS['zh']['help.body']
    en = _TRANSLATIONS['en']['help.body']
    # 明确告诉用户多站点、批量、格式与后向兼容，避免只存在于代码里。
    assert '多站点' in zh and '批量' in zh
    assert '站点地址|APIKey|模型名' in zh
    assert 'multi-site' in en and 'site' in en
    assert 'url|api_key|model' in en
    for lang in LANGS:
        assert '醍醐测智' in _TRANSLATIONS[lang]['help.body']


def test_tihudace_keys_have_language_parity():
    zh = {key for key in _TRANSLATIONS['zh'] if key.startswith('tihudace.')}
    en = {key for key in _TRANSLATIONS['en'] if key.startswith('tihudace.')}
    missing_en = sorted(zh - en)
    missing_zh = sorted(en - zh)
    assert missing_en == [], f'English is missing: {missing_en}'
    assert missing_zh == [], f'Chinese is missing: {missing_zh}'


def test_batch_and_spec_keys_exist_in_both_languages():
    for lang in LANGS:
        for key in (
            'tihudace.batch.working',
            'tihudace.batch.header',
            'tihudace.batch.item',
            'tihudace.batch.state.ok',
            'tihudace.batch.item_failed',
            'tihudace.batch.none',
            'tihudace.batch.issue_header',
            'tihudace.batch.issue_line',
            'tihudace.batch.issue_more',
            'tihudace.spec.reason.invalid_format',
            'tihudace.spec.reason.invalid_base_url',
            'tihudace.spec.reason.missing_model',
            'tihudace.spec.reason.missing_api_key',
            'tihudace.spec.reason.too_many_targets',
        ):
            assert key in _TRANSLATIONS[lang], f'{lang} missing {key}'


def test_every_spec_reason_has_a_message():
    from tihudace_targets import TargetSpecError

    reasons = set(TargetSpecError.REASONS) | {'too_many_targets'}
    for lang in LANGS:
        for reason in reasons:
            key = f'tihudace.spec.reason.{reason}'
            assert key in _TRANSLATIONS[lang], f'{lang} missing {key}'


@pytest.mark.parametrize('lang', LANGS)
def test_batch_messages_format_with_expected_arguments(lang):
    def t(key, **kwargs):
        return translate(lang, key, **kwargs)

    assert '3' in t('tihudace.batch.working', count=3)
    assert '/2' in t('tihudace.batch.header', ok=1, total=2)
    assert '主站' in t('tihudace.batch.item', name='主站', model='m1', state='ok')
    assert '主站' in t('tihudace.batch.item_failed', name='主站', model='m1', reason='x')
    assert '2' in t('tihudace.batch.issue_line', index=2, reason='r', preview='p')
    assert '4' in t('tihudace.batch.issue_more', count=4)


def test_no_batch_message_leaks_a_placeholder():
    for lang in LANGS:
        for key, message in _TRANSLATIONS[lang].items():
            if key.startswith('tihudace.batch.'):
                assert '{' not in translate(lang, key, **_sample_args(key)) or True
                # 用真实参数渲染后不应残留占位符
                rendered = translate(lang, key, **_sample_args(key))
                assert '{' not in rendered, f'{lang} {key} left a placeholder: {rendered}'


def _sample_args(key):
    return {
        'count': 2,
        'ok': 1,
        'total': 2,
        'name': 'n',
        'model': 'm',
        'state': 's',
        'reason': 'r',
        'index': 1,
        'preview': 'p',
    }
