"""多站点配置解析的离线单元测试。"""

import pytest

from tihudace_targets import (
    DEFAULT_MAX_TARGETS,
    TargetIssue,
    TargetSpecError,
    TihudaceTarget,
    mask_secret,
    normalize_base_url,
    parse_spec,
    parse_target_specs,
)


def test_three_and_four_field_rows():
    target = parse_spec('https://a.example.com|sk-aaa|model-a')
    assert (target.name, target.base_url, target.api_key, target.model) == (
        '', 'https://a.example.com', 'sk-aaa', 'model-a')

    named = parse_spec('主站|https://b.example.com|sk-bbb|model-b')
    assert (named.name, named.base_url, named.api_key, named.model) == (
        '主站', 'https://b.example.com', 'sk-bbb', 'model-b')


def test_fullwidth_separator_and_whitespace():
    target = parse_spec('  镜像 ｜ https://c.example.com ｜ sk-ccc ｜ model-c  ')
    assert (target.name, target.base_url, target.api_key, target.model) == (
        '镜像', 'https://c.example.com', 'sk-ccc', 'model-c')


@pytest.mark.parametrize('row', [
    '', '   ', '# 注释', 'site-without-separator', 'a|b', 'a|b|c|d|e',
])
def test_invalid_forms_are_classified(row):
    if row.strip().startswith('#') or not row.strip():
        assert not parse_target_specs([row])[0]
        return
    with pytest.raises(TargetSpecError) as caught:
        parse_spec(row)
    assert caught.value.reason in TargetSpecError.REASONS


def test_url_normalization_adds_scheme_and_strips_trailing_slash():
    assert normalize_base_url('api.example.com/') == 'https://api.example.com'
    assert normalize_base_url('http://api.example.com/v1/') == 'http://api.example.com/v1'
    for bad in ['ftp://api.example.com', 'https://', '/relative/path', 'file:///tmp/x']:
        with pytest.raises(TargetSpecError) as caught:
            normalize_base_url(bad)
        assert caught.value.reason == 'invalid_base_url'


def test_missing_model_and_key_reasons():
    with pytest.raises(TargetSpecError) as caught:
        parse_spec('https://a.example.com|sk-a|')
    assert caught.value.reason == 'missing_model'

    with pytest.raises(TargetSpecError) as caught:
        parse_spec('https://a.example.com||model-a')
    assert caught.value.reason == 'missing_api_key'


def test_entry_key_falls_back_to_shared_key_and_bearer_prefix_is_stripped():
    targets, issues = parse_target_specs(
        ['https://a.example.com||model-a', 'https://b.example.com|Bearer sk-bbb|model-b'],
        fallback_api_key='sk-shared',
    )
    assert issues == []
    assert [t.api_key for t in targets] == ['sk-shared', 'sk-bbb']


def test_invalid_rows_do_not_block_valid_ones():
    targets, issues = parse_target_specs([
        'https://good.example.com|sk-1|m1',
        'broken',
        'https://good2.example.com|sk-2|m2',
        'https://x.example.com|sk-3|',
    ], fallback_api_key='')
    assert [t.model for t in targets] == ['m1', 'm2']
    assert [(i.index, i.reason) for i in issues] == [
        (2, 'invalid_format'), (4, 'missing_model')]


def test_blank_comment_and_duplicate_rows_are_skipped():
    targets, issues = parse_target_specs([
        '', '   ', '# 主站', 'https://a.example.com|sk-a|m1',
        'https://a.example.com|sk-a|m1',
    ])
    assert len(targets) == 1
    assert issues == []


def test_max_targets_reports_overflow_without_dropping_indices():
    rows = [f'https://s{i}.example.com|sk-{i}|m{i}' for i in range(DEFAULT_MAX_TARGETS + 2)]
    targets, issues = parse_target_specs(rows, fallback_api_key='')
    assert len(targets) == DEFAULT_MAX_TARGETS
    assert [i.reason for i in issues] == ['too_many_targets', 'too_many_targets']
    assert [i.index for i in issues] == [DEFAULT_MAX_TARGETS + 1, DEFAULT_MAX_TARGETS + 2]


def test_issue_preview_masks_secret():
    # 合法行不产生 issue；坏行里的密钥同样必须脱敏。
    _, issues = parse_target_specs([
        'https://a.example.com|sk-super-secret|m1',
        'https://b.example.com|sk-another-secret|',
        'junk',
    ])
    assert [i.index for i in issues] == [2, 3]
    previews = ' '.join(i.preview for i in issues)
    assert 'sk-another-secret' not in previews
    assert issues[0].preview.startswith('https://b.example.com|*')
    assert issues[0].preview.rstrip('|').endswith('cret')
    assert mask_secret('sk-super-secret').endswith('cret')
    assert mask_secret('') == ''


def test_label_defaults_to_model_and_host():
    assert TihudaceTarget('', 'https://api.example.com', 'k', 'model-a').label == 'model-a@api.example.com'
    assert TihudaceTarget('主站', 'https://api.example.com', 'k', 'model-a').label == '主站'


def test_none_and_scalar_inputs_are_tolerated():
    assert parse_target_specs(None) == ([], [])
    targets, issues = parse_target_specs('https://a.example.com|sk-a|m1')
    assert len(targets) == 1 and issues == []
    target = TihudaceTarget('n', 'https://a', 'k', 'm')
    assert TargetIssue(index=1, reason='invalid_format', preview=target.model).reason == 'invalid_format'
