"""批量执行编排的离线单元测试（不访问网络、不依赖 astrbot）。"""

import asyncio

import pytest

from tihudace_batch import (
    DEFAULT_CONCURRENCY,
    TargetOutcome,
    first_failure_status,
    run_targets,
    successful,
)
from tihudace_targets import TihudaceTarget


def make_targets(count):
    return [
        TihudaceTarget(name=f'站点{i + 1}', base_url=f'https://s{i + 1}.example.com',
                       api_key=f'sk-{i + 1}', model=f'm{i + 1}')
        for i in range(count)
    ]


def test_success_keeps_configuration_order():
    targets = make_targets(4)

    async def run():
        async def run_one(target):
            # 后面的站点先返回，验证结果仍按配置顺序排列。
            await asyncio.sleep(0.01 * (4 - int(target.model[1:])))
            return 'OK', f'/tmp/{target.model}.png'

        return await run_targets(targets, run_one, concurrency=4)

    outcomes = asyncio.run(run())
    assert [o.target.model for o in outcomes] == ['m1', 'm2', 'm3', 'm4']
    assert all(o.ok for o in outcomes)
    assert [o.png for o in outcomes][0] == '/tmp/m1.png'


def test_single_failure_does_not_stop_other_sites():
    targets = make_targets(3)

    async def run():
        async def run_one(target):
            status = {
                'm1': 'MODEL_TIMEOUT',
                'm2': 'OK',
                'm3': 'RENDER_FAILED',
            }[target.model]
            return status, (f'/tmp/{target.model}.png' if status == 'OK' else '')

        return await run_targets(targets, run_one)

    outcomes = asyncio.run(run())
    assert [(o.target.model, o.status, o.ok) for o in outcomes] == [
        ('m1', 'MODEL_TIMEOUT', False),
        ('m2', 'OK', True),
        ('m3', 'RENDER_FAILED', False),
    ]
    assert [o.target.model for o in successful(outcomes)] == ['m2']
    assert first_failure_status(outcomes) == 'MODEL_TIMEOUT'


def test_exception_is_contained_and_reported_as_internal_error():
    targets = make_targets(2)

    async def run():
        async def run_one(target):
            if target.model == 'm1':
                raise RuntimeError('boom: sk-leaked-key')
            return 'OK', '/tmp/m2.png'

        return await run_targets(targets, run_one)

    outcomes = asyncio.run(run())
    assert outcomes[0].status == 'INTERNAL_ERROR'
    assert outcomes[0].png == ''
    assert outcomes[1].ok
    assert 'sk-leaked-key' not in outcomes[0].status


def test_ok_without_png_is_not_a_success():
    outcome = TargetOutcome(target=make_targets(1)[0], status='OK', png='')
    assert outcome.ok is False


def test_concurrency_limit_is_applied():
    targets = make_targets(6)
    state = {'active': 0, 'peak': 0}

    async def run():
        async def run_one(target):
            state['active'] += 1
            state['peak'] = max(state['peak'], state['active'])
            await asyncio.sleep(0.02)
            state['active'] -= 1
            return 'OK', f'/tmp/{target.model}.png'

        return await run_targets(targets, run_one, concurrency=2)

    outcomes = asyncio.run(run())
    assert len(outcomes) == 6
    assert state['peak'] <= 2


def test_invalid_concurrency_falls_back_to_default():
    targets = make_targets(2)

    async def run():
        async def run_one(target):
            return 'OK', '/tmp/x.png'

        return await run_targets(targets, run_one, concurrency=0)

    assert len(asyncio.run(run())) == 2
    assert DEFAULT_CONCURRENCY >= 1


def test_empty_target_list_returns_empty_result():
    async def run():
        async def run_one(target):
            raise AssertionError('should not be called')

        return await run_targets([], run_one)

    assert asyncio.run(run()) == []
    assert first_failure_status([], default='MODEL_FAILED') == 'MODEL_FAILED'


def test_cancellation_propagates():
    targets = make_targets(1)

    async def run():
        async def run_one(target):
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await run_targets(targets, run_one)
