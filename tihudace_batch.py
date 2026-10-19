"""醍醐测智批量执行编排。

对每个站点执行「请求模型 → 提取 HTML → 渲染 PNG」，单站点失败不影响其他站点，
结果保持配置顺序。本模块不导入 astrbot，可离线单元测试。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Sequence

try:  # 作为插件子模块加载（AstrBot 运行时）
    from .tihudace_targets import TihudaceTarget
except ImportError:  # 作为独立模块导入（离线单元测试）
    from tihudace_targets import TihudaceTarget

# 同时打向上游的站点数：过多会争抢额度，也容易把本机 Chromium 打满。
DEFAULT_CONCURRENCY = 2

# 单站点执行后可能返回的状态码；与单站点路径保持一致，便于复用文案。
STATUS_OK = 'OK'

# 执行器返回 (状态码, png 路径)
RunOne = Callable[[TihudaceTarget], Awaitable[tuple]]


@dataclass(frozen=True)
class TargetOutcome:
    """一个站点的执行结果。status 为 OK 时 png 非空。"""

    target: TihudaceTarget
    status: str
    png: str = ''

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK and bool(self.png)


async def run_targets(
    targets: Sequence[TihudaceTarget],
    run_one: RunOne,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> List[TargetOutcome]:
    """按并发上限执行所有站点，返回与输入同序的结果。

    单站点抛出的异常被收敛为 INTERNAL_ERROR，不中断其他站点；
    取消信号（asyncio.CancelledError）照常向上传播。
    """
    items = list(targets or [])
    if not items:
        return []
    limit = concurrency if isinstance(concurrency, int) and concurrency > 0 else DEFAULT_CONCURRENCY
    semaphore = asyncio.Semaphore(min(limit, len(items)))

    async def execute(target: TihudaceTarget) -> TargetOutcome:
        async with semaphore:
            try:
                status, png = await run_one(target)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 异常文本可能含上游响应或密钥，只保留稳定状态码。
                return TargetOutcome(target=target, status='INTERNAL_ERROR')
        return TargetOutcome(
            target=target,
            status=str(status or 'INTERNAL_ERROR'),
            png=str(png or ''),
        )

    return list(await asyncio.gather(*(execute(target) for target in items)))


def successful(outcomes: Sequence[TargetOutcome]) -> List[TargetOutcome]:
    """筛出真正拿到图片的站点。"""
    return [outcome for outcome in outcomes or [] if outcome.ok]


def first_failure_status(outcomes: Sequence[TargetOutcome], default: str = 'MODEL_FAILED') -> str:
    """取第一个未成功站点的状态，用于单站点风格的失败上报。"""
    for outcome in outcomes or []:
        if not outcome.ok:
            return outcome.status or default
    return default
