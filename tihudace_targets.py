"""醍醐测智多站点配置解析。

配置形态（`codex_settings.targets`，字符串列表，每行一个站点）：

    base_url|api_key|model
    name|base_url|api_key|model

也接受全角竖线 `｜`、`#` 开头的注释行与空行。列表为空时沿用
`codex_settings.base_url` / `api_key` / `model` 的单站点配置（向后兼容）。

本模块只做纯解析与校验，不导入 astrbot，便于离线单元测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

# 一次批量测智最多执行的站点数：每次都会真实调用模型并渲染图片，
# 无上限会拖垮上游额度与本机 Chromium。
DEFAULT_MAX_TARGETS = 10

# 中文输入法常见的全角竖线
_SEPARATORS = ('|', '｜')


class TargetSpecError(ValueError):
    """一条站点配置无效；reason 为稳定代号，用于多语言文案。"""

    REASONS = frozenset({
        'invalid_format', 'invalid_base_url', 'missing_model', 'missing_api_key',
    })

    def __init__(self, reason: str):
        self.reason = reason if reason in self.REASONS else 'invalid_format'
        super().__init__(self.reason)


@dataclass(frozen=True)
class TihudaceTarget:
    """一个可调用的模型站点。"""

    name: str
    base_url: str
    api_key: str
    model: str

    @property
    def label(self) -> str:
        """展示用名称：显式名称优先，否则 `model@host`。"""
        if self.name:
            return self.name
        host = urlsplit(self.base_url).hostname or self.base_url
        return f'{self.model}@{host}'


@dataclass(frozen=True)
class TargetIssue:
    """一条被跳过的配置；index 从 1 开始，preview 已脱敏。"""

    index: int
    reason: str
    preview: str


def mask_secret(value: str, keep: int = 4) -> str:
    """脱敏密钥，只保留末尾若干位，避免写进日志或群消息。"""
    text = str(value or '')
    if not text:
        return ''
    if len(text) <= keep:
        return '*' * len(text)
    return '*' * (len(text) - keep) + text[-keep:]


def normalize_base_url(raw: str) -> str:
    """校验并规范化站点地址；只接受 http/https 且带主机名。"""
    text = str(raw or '').strip()
    if not text:
        raise TargetSpecError('invalid_base_url')
    candidate = text if '://' in text else f'https://{text}'
    candidate = candidate.rstrip('/')
    try:
        parts = urlsplit(candidate)
    except ValueError:
        raise TargetSpecError('invalid_base_url') from None
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise TargetSpecError('invalid_base_url')
    return candidate


def split_spec(spec: str) -> List[str]:
    """按竖线切分一条配置，忽略全角/半角混用。"""
    text = str(spec or '').strip()
    if text.startswith('#'):
        return []
    for separator in _SEPARATORS[1:]:
        text = text.replace(separator, _SEPARATORS[0])
    return [part.strip() for part in text.split(_SEPARATORS[0])]


def parse_spec(spec: str, fallback_api_key: str = '') -> TihudaceTarget:
    """解析单条站点配置。

    三段 `base_url|api_key|model` 或四段 `name|base_url|api_key|model`；
    api_key 留空时回退到 `codex_settings.api_key`。
    """
    parts = split_spec(spec)
    if not parts:
        raise TargetSpecError('invalid_format')
    if len(parts) == 4:
        name, base_raw, key_raw, model_raw = parts
    elif len(parts) == 3:
        name = ''
        base_raw, key_raw, model_raw = parts
    else:
        raise TargetSpecError('invalid_format')
    base_url = normalize_base_url(base_raw)
    model = str(model_raw or '').strip()
    if not model:
        raise TargetSpecError('missing_model')
    api_key = str(key_raw or '').strip()
    if not api_key:
        api_key = str(fallback_api_key or '').strip()
    if not api_key:
        raise TargetSpecError('missing_api_key')
    if api_key.lower().startswith('bearer '):
        api_key = api_key[7:].strip()
        if not api_key:
            raise TargetSpecError('missing_api_key')
    return TihudaceTarget(name=name, base_url=base_url, api_key=api_key, model=model)


def parse_target_specs(
    raw: Optional[Sequence[object]],
    fallback_api_key: str = '',
    max_targets: int = DEFAULT_MAX_TARGETS,
) -> Tuple[List[TihudaceTarget], List[TargetIssue]]:
    """解析站点列表，返回（可用站点, 被跳过的配置）。

    坏配置不会中断整批：可用站点照常执行，问题条目单独报告。
    超过 `max_targets` 的部分被跳过并报告，避免一次请求过多模型。
    """
    if raw is None:
        return [], []
    if isinstance(raw, (str, bytes)):
        entries: Iterable[object] = [raw]
    else:
        try:
            entries = list(raw)
        except TypeError:
            entries = [raw]
    targets: List[TihudaceTarget] = []
    issues: List[TargetIssue] = []
    seen = set()
    for index, entry in enumerate(entries, 1):
        text = str(entry or '').strip()
        if not text or text.startswith('#'):
            continue
        try:
            target = parse_spec(text, fallback_api_key)
        except TargetSpecError as e:
            issues.append(TargetIssue(index=index, reason=e.reason, preview=_preview(text)))
            continue
        identity = (target.base_url, target.model, target.api_key)
        if identity in seen:
            continue
        seen.add(identity)
        if len(targets) >= max_targets:
            issues.append(TargetIssue(index=index, reason='too_many_targets', preview=_preview(text)))
            continue
        targets.append(target)
    return targets, issues


def _preview(text: str) -> str:
    """脱敏后的配置预览：把疑似密钥段整体遮蔽。"""
    parts = split_spec(text)
    if len(parts) == 4:
        name, base, key, model = parts
        return '|'.join([name, base, mask_secret(key), model])
    if len(parts) == 3:
        base, key, model = parts
        return '|'.join([base, mask_secret(key), model])
    return mask_secret(text)
