"""Offline regression for the PK feature (no real quota or binding writes).

Uses the real NewApiCore SQLite path against an in-memory database, real
bindings lookups and the real PK state machine; New API money operations are
replaced by an in-memory balance ledger so no HTTP or quota mutation occurs.
"""
import asyncio
import importlib.util
from datetime import datetime
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import httpx
from astrbot.api import AstrBotConfig
from astrbot.api.message_components import At
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
from compat_plugin.pk_logic import PkLogic
from compat_plugin.main import NewApiSuitePlugin

SENDER = "PK_SENDER_PRIVATE_SENTINEL"
SENDER2 = "PK_SENDER2_PRIVATE_SENTINEL"
GROUP = "PK_GROUP_PRIVATE_SENTINEL"
CHALLENGER_SITE = 13
OPPONENT_SITE = 26


class PkLogicTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config = AstrBotConfig.__new__(AstrBotConfig)
        config.update({
            "binding_settings": {"quota_display_ratio": 100},
            "pk_settings": {"enabled": True, "expiry_seconds": 300,
                            "auto_accept_admin_site": 1, "max_stake": 100},
        })
        self.config = config
        self.core = NewApiCore(config)
        self.connection = await aiosqlite.connect(":memory:")
        self.addAsyncCleanup(self.connection.close)
        self.connection.row_factory = aiosqlite.Row
        self.core.db_mode = "sqlite"
        self.core.db_conn = self.connection
        for ddl in NewApiCore._TRANSFER_SQLITE_DDL.values():
            await self.connection.execute(ddl)
        await self.connection.commit()

        guard = patch.object(
            httpx.AsyncClient, "request", new_callable=AsyncMock,
            side_effect=AssertionError("Real HTTP is forbidden in PK tests"),
        )
        self.blocked_http = guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.blocked_http.assert_not_awaited)

        # In-memory money ledger replaces every New API quota call.
        self.balances = {CHALLENGER_SITE: 0, OPPONENT_SITE: 0}
        self.quota_calls = []

        async def get_api_user_data(site):
            return {"id": site, "quota": self.balances.get(site, 0)}

        async def manage_user_quota(site, action, value):
            self.quota_calls.append((site, action, value))
            current = self.balances.get(site, 0)
            if action == "subtract":
                if value > current:
                    return False
                self.balances[site] = current - value
            elif action == "add":
                self.balances[site] = current + value
            else:
                return False
            return True

        self.core.get_api_user_data = AsyncMock(side_effect=get_api_user_data)
        self.core.manage_user_quota = AsyncMock(side_effect=manage_user_quota)
        self.pk = PkLogic(self.config, self.core)
        self.pk._now_fn = lambda: 1000.0

    async def seed(self):
        await self.connection.execute(
            "INSERT INTO newapi_bindings (qq_id, website_user_id) VALUES (?, ?)",
            (70001, CHALLENGER_SITE))
        await self.connection.execute(
            "INSERT INTO newapi_bindings (qq_id, website_user_id) VALUES (?, ?)",
            (80001, OPPONENT_SITE))
        await self.connection.commit()

    async def pending_rows(self):
        cur = await self.connection.execute(
            "SELECT id, status, challenger_site, opponent_site, stake_raw, "
            "expires_at, winner_site, final_ms_digit FROM newapi_pk_matches")
        return [dict(row) for row in await cur.fetchall()]

    async def test_create_deducts_stake_and_persists_pending(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        status, details = await self.pk.create_challenge("qq:70001", "26", 100.0)
        self.assertEqual(status, "CREATED")
        self.assertEqual(details["challenger_site"], CHALLENGER_SITE)
        self.assertEqual(details["opponent_site"], OPPONENT_SITE)
        self.assertEqual(details["stake_raw"], 10000)
        self.assertEqual(self.balances[CHALLENGER_SITE], 40000)
        rows = await self.pending_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "PENDING")
        self.assertEqual(rows[0]["stake_raw"], 10000)

    async def test_insufficient_balance_prompts_without_deduct_or_row(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 5000
        status, details = await self.pk.create_challenge("qq:70001", "26", 100.0)
        self.assertEqual(status, "INSUFFICIENT_BALANCE")
        self.assertEqual(self.balances[CHALLENGER_SITE], 5000)
        self.assertEqual(await self.pending_rows(), [])
        self.core.manage_user_quota.assert_not_awaited()

    async def test_invalid_amounts_are_rejected(self):
        await self.seed()
        for bad in (0, -1, 0.0, "abc", float("inf"), float("nan"), None):
            with self.subTest(amount=bad):
                status, _ = await self.pk.create_challenge("qq:70001", "26", bad)
                self.assertEqual(status, "INVALID_AMOUNT")
        self.assertEqual(await self.pending_rows(), [])

    async def test_self_challenge_missing_target_and_disabled(self):
        await self.seed()
        self.assertEqual((await self.pk.create_challenge("qq:70001", "13", 1))[0], "CANNOT_PK_SELF")
        self.assertEqual((await self.pk.create_challenge("qq:70001", "999", 1))[0], "TARGET_NOT_FOUND")
        self.config["pk_settings"]["enabled"] = False
        self.assertEqual((await self.pk.create_challenge("qq:70001", "26", 1))[0], "DISABLED")

    async def test_duplicate_pending_between_pair_is_rejected(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        self.assertEqual((await self.pk.create_challenge("qq:70001", "26", 100))[0], "CREATED")
        self.assertEqual((await self.pk.create_challenge("qq:70001", "26", 100))[0], "ALREADY_PENDING")
        self.assertEqual((await self.pk.create_challenge("qq:80001", "13", 100))[0], "ALREADY_PENDING")
        self.assertEqual(self.balances[CHALLENGER_SITE], 40000)

    async def test_even_digit_settles_for_challenger(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        self.balances[OPPONENT_SITE] = 50000
        await self.pk.create_challenge("qq:70001", "26", 100.0)
        self.pk._now_fn = lambda: 1000.123  # 数字和尾数 4（双数）→ challenger wins
        status, details = await self.pk.accept_challenge("qq:80001", "13")
        self.assertEqual(status, "SETTLED")
        expected_ts = datetime.fromtimestamp(1000.123).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        expected_digit = PkLogic._timestamp_digit(expected_ts)
        self.assertEqual(details["digit"], expected_digit)
        self.assertEqual(details["digit"] % 2, 0)
        self.assertEqual(details["digit_sum"], sum(int(c) for c in expected_ts if c.isdigit()))
        self.assertEqual(details["winner_site"], CHALLENGER_SITE)
        self.assertEqual(details["loser_site"], OPPONENT_SITE)
        self.assertTrue(details["challenger_wins"])
        self.assertEqual(self.balances[CHALLENGER_SITE], 60000)
        self.assertEqual(self.balances[OPPONENT_SITE], 40000)
        self.assertEqual(details["winner_balance"], 600.0)
        self.assertEqual(details["loser_balance"], 400.0)
        rows = await self.pending_rows()
        self.assertEqual(rows[0]["status"], "SETTLED")
        self.assertEqual(rows[0]["winner_site"], CHALLENGER_SITE)

    async def test_odd_digit_settles_for_opponent(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        self.balances[OPPONENT_SITE] = 50000
        await self.pk.create_challenge("qq:70001", "26", 100.0)
        self.pk._now_fn = lambda: 1000.124  # 数字和尾数 5（单数）→ opponent wins
        status, details = await self.pk.accept_challenge("qq:80001", "13")
        self.assertEqual(status, "SETTLED")
        expected_ts = datetime.fromtimestamp(1000.124).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        expected_digit = PkLogic._timestamp_digit(expected_ts)
        self.assertEqual(details["digit"], expected_digit)
        self.assertEqual(details["digit"] % 2, 1)
        self.assertEqual(details["winner_site"], OPPONENT_SITE)
        self.assertFalse(details["challenger_wins"])
        self.assertEqual(self.balances[CHALLENGER_SITE], 40000)
        self.assertEqual(self.balances[OPPONENT_SITE], 60000)
        self.assertEqual(details["winner_balance"], 600.0)
        self.assertEqual(details["loser_balance"], 400.0)

    async def test_expired_challenge_refunds_and_cannot_be_accepted(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        self.balances[OPPONENT_SITE] = 50000
        await self.pk.create_challenge("qq:70001", "26", 100.0)
        self.pk._now_fn = lambda: 1000.0 + 301  # beyond 300s expiry
        status, _ = await self.pk.accept_challenge("qq:80001", "13")
        self.assertEqual(status, "EXPIRED")
        self.assertEqual(self.balances[CHALLENGER_SITE], 50000)
        self.assertEqual(self.balances[OPPONENT_SITE], 50000)
        self.assertEqual((await self.pending_rows())[0]["status"], "EXPIRED")

    async def test_accept_insufficient_balance_keeps_challenge_pending(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        self.balances[OPPONENT_SITE] = 5000
        await self.pk.create_challenge("qq:70001", "26", 100.0)
        status, details = await self.pk.accept_challenge("qq:80001", "13")
        self.assertEqual(status, "ACCEPT_INSUFFICIENT_BALANCE")
        self.assertEqual(details["need"], 100.0)
        self.assertEqual(self.balances[OPPONENT_SITE], 5000)
        self.assertEqual((await self.pending_rows())[0]["status"], "PENDING")

    async def test_multiple_pending_require_identifier(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        self.balances[39] = 50000
        await self.connection.execute(
            "INSERT INTO newapi_bindings (qq_id, website_user_id) VALUES (?, ?)",
            (90001, 39))
        await self.connection.commit()
        await self.pk.create_challenge("qq:70001", "26", 100.0)
        await self.pk.create_challenge("qq:90001", "26", 100.0)
        status, details = await self.pk.accept_challenge("qq:80001", None)
        self.assertEqual(status, "MULTIPLE_PENDING")
        self.assertEqual(details["count"], 2)

    async def test_concurrent_accepts_settle_exactly_once(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        self.balances[OPPONENT_SITE] = 50000
        await self.pk.create_challenge("qq:70001", "26", 100.0)
        self.pk._now_fn = lambda: 1000.124  # 单数 → opponent (site 26) wins
        results = await asyncio.gather(
            self.pk.accept_challenge("qq:80001", "13"),
            self.pk.accept_challenge("qq:80001", "13"),
        )
        settled = [r for r in results if r[0] == "SETTLED"]
        self.assertEqual(len(settled), 1)
        self.assertEqual([r[0] for r in results].count("NOT_FOUND"), 1)
        self.assertEqual(self.balances[CHALLENGER_SITE], 40000)
        self.assertEqual(self.balances[OPPONENT_SITE], 60000)

    async def test_payout_failure_refunds_both_sides(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        self.balances[OPPONENT_SITE] = 50000
        await self.pk.create_challenge("qq:70001", "26", 100.0)
        self.pk._now_fn = lambda: 1000.124
        original = self.core.manage_user_quota

        async def flaky(site, action, value):
            if action == "add" and value == 20000:
                return False  # only the winner payout fails; refunds must succeed
            return await original(site, action, value)

        self.core.manage_user_quota = AsyncMock(side_effect=flaky)
        status, _ = await self.pk.accept_challenge("qq:80001", "13")
        self.assertEqual(status, "SETTLE_FAILED_REFUNDED")
        self.assertEqual(self.balances[CHALLENGER_SITE], 50000)
        self.assertEqual(self.balances[OPPONENT_SITE], 50000)
        self.assertEqual((await self.pending_rows())[0]["status"], "REFUNDED")

    async def test_insert_failure_refunds_challenger(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        self.pk._insert_match = AsyncMock(return_value=None)
        status, _ = await self.pk.create_challenge("qq:70001", "26", 100.0)
        self.assertEqual(status, "DB_FAILED")
        self.assertEqual(self.balances[CHALLENGER_SITE], 50000)

    async def seed_admin(self):
        await self.connection.execute(
            "INSERT INTO newapi_bindings (qq_id, website_user_id) VALUES (?, ?)",
            (60001, 1))
        await self.connection.commit()
        self.balances[1] = 0

    async def test_auto_accept_admin_settles_immediately(self):
        await self.seed()
        await self.seed_admin()
        self.balances[CHALLENGER_SITE] = 50000
        self.balances[1] = 50000
        self.pk._now_fn = lambda: 1000.124  # 数字和尾数 5（单数）→ admin(opponent) wins
        status, details = await self.pk.create_challenge("qq:70001", "1", 100.0)
        self.assertEqual(status, "AUTO_SETTLED")
        self.assertEqual(details["winner_site"], 1)
        self.assertEqual(self.balances[CHALLENGER_SITE], 40000)
        self.assertEqual(self.balances[1], 60000)
        self.assertEqual(details["winner_balance"], 600.0)
        self.assertEqual(details["loser_balance"], 400.0)
        rows = await self.pending_rows()
        self.assertEqual(rows[0]["status"], "SETTLED")
        self.assertEqual(rows[0]["winner_site"], 1)

    async def test_auto_accept_admin_insufficient_balance_refunds(self):
        await self.seed()
        await self.seed_admin()
        self.balances[CHALLENGER_SITE] = 50000
        self.balances[1] = 5000
        status, _ = await self.pk.create_challenge("qq:70001", "1", 100.0)
        self.assertEqual(status, "AUTO_ACCEPT_FAILED_REFUNDED")
        self.assertEqual(self.balances[CHALLENGER_SITE], 50000)
        self.assertEqual(self.balances[1], 5000)
        self.assertEqual((await self.pending_rows())[0]["status"], "REFUNDED")

    async def test_auto_accept_disabled_when_site_is_zero(self):
        await self.seed()
        await self.seed_admin()
        self.config["pk_settings"]["auto_accept_admin_site"] = 0
        self.balances[CHALLENGER_SITE] = 50000
        status, details = await self.pk.create_challenge("qq:70001", "1", 100.0)
        self.assertEqual(status, "CREATED")
        self.assertEqual(details["opponent_site"], 1)
        self.assertEqual((await self.pending_rows())[0]["status"], "PENDING")


    async def test_stake_over_max_is_rejected_without_deduct(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        status, details = await self.pk.create_challenge("qq:70001", "26", 150.0)
        self.assertEqual(status, "STAKE_TOO_LARGE")
        self.assertEqual(details["max"], 100)
        self.assertEqual(self.balances[CHALLENGER_SITE], 50000)
        self.assertEqual(await self.pending_rows(), [])
        self.core.manage_user_quota.assert_not_awaited()

    async def test_stake_within_max_is_allowed(self):
        await self.seed()
        self.balances[CHALLENGER_SITE] = 50000
        status, details = await self.pk.create_challenge("qq:70001", "26", 100.0)
        self.assertEqual(status, "CREATED")
        self.assertEqual(details["stake_raw"], 10000)
        self.assertEqual(self.balances[CHALLENGER_SITE], 40000)

    async def test_max_stake_zero_disables_cap(self):
        await self.seed()
        self.config["pk_settings"]["max_stake"] = 0
        self.balances[CHALLENGER_SITE] = 1000000
        status, _ = await self.pk.create_challenge("qq:70001", "26", 150.0)
        self.assertEqual(status, "CREATED")

    def test_timestamp_digit_sums_all_digits(self):
        self.assertEqual(PkLogic._timestamp_digit("2026-09-24 16:39:56.283"), 8)
        self.assertEqual(PkLogic._timestamp_digit("1970-01-01 08:16:40.124"), 5)
        self.assertEqual(PkLogic._timestamp_digit("0000-00-00 00:00:00.000"), 0)


class PkCommandHandlerTests(unittest.IsolatedAsyncioTestCase):
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
            "binding_settings": {"quota_display_ratio": 100},
            "pk_settings": {"enabled": True, "expiry_seconds": 300, "max_stake": 100},
        })
        self.plugin = object.__new__(NewApiSuitePlugin)
        self.plugin.config = config
        self.plugin.lang = "zh"
        self.plugin._reply = lambda event, text: text
        self.plugin.core = SimpleNamespace(
            get_user_by_identity=AsyncMock(return_value={"website_user_id": 13}),
        )
        self.plugin.core.get_openid_by_website_id = AsyncMock(
            side_effect=lambda site: {"openid": f"OPENID_{site}"})
        self.plugin.core.get_user_by_website_id = AsyncMock(return_value=None)
        self.plugin._refresh_balance_cache = AsyncMock()
        created = {"challenger_site": 13, "opponent_site": 26,
                   "stake_raw": 10000, "stake_display": 100.0}
        settled = {"challenger_site": 13, "opponent_site": 26,
                   "winner_site": 13, "loser_site": 26, "stake_raw": 10000,
                   "pot_display": 200.0, "digit": 2, "challenger_wins": True,
                   "digit_sum": 52,
                   "digit_expression": "2+0+2+6+0+9+2+4+1+5+3+8+2+2+2+2+2",
                   "winner_balance": 600.0, "loser_balance": 400.0,
                   "settled_time": "2026-09-24 15:38:22.222"}
        self.plugin.pk_handler = SimpleNamespace(
            create_challenge=AsyncMock(return_value=("CREATED", created)),
            accept_challenge=AsyncMock(return_value=("SETTLED", settled)),
        )

    async def event(self, text, sender=SENDER):
        raw = PatchedGroupMessage(None, "offline-event", {
            "id": "offline-message", "group_openid": GROUP,
            "author": {"member_openid": sender, "username": "offline"},
            "content": text, "attachments": [], "mentions": [],
            "timestamp": "2026-01-01T00:00:00Z",
        })
        message = await QQOfficialPlatformAdapter._parse_from_qqofficial(
            raw, MessageType.GROUP_MESSAGE)
        message.group_id = GROUP
        message.session_id = GROUP
        return self.platform.create_event(message)

    def parse(self, name, event, handler):
        event.is_at_or_wake_command = True
        command = CommandFilter(name)
        command.init_handler_md(SimpleNamespace(handler=handler))
        self.assertTrue(command.filter(event, self.plugin.config))
        return event.get_extra("parsed_params")

    async def collect(self, generator):
        return [item async for item in generator]

    async def test_pk_command_parses_target_and_amount(self):
        event = await self.event("PK 26 100")
        params = self.parse("PK", event, NewApiSuitePlugin.handle_pk_command)
        replies = await self.collect(self.plugin.handle_pk_command(event, **params))
        self.plugin.pk_handler.create_challenge.assert_awaited_once_with(
            "openid:" + SENDER, "26", 100.0)
        result = replies[0]
        self.assertEqual([c for c in result.chain if isinstance(c, At)], [])
        plain = "".join(getattr(c, "text", "") for c in result.chain)
        self.assertTrue(plain.startswith("<@OPENID_26> "))
        self.assertIn("网站ID 13 向 网站ID 26 发起 PK", plain)

    async def test_pk_command_bad_amount_never_creates(self):
        event = await self.event("PK 26 abc")
        params = self.parse("PK", event, NewApiSuitePlugin.handle_pk_command)
        replies = await self.collect(self.plugin.handle_pk_command(event, **params))
        self.assertIn("押注金额必须是大于 0 的有效数字", replies[0])
        self.plugin.pk_handler.create_challenge.assert_not_awaited()

    async def test_pk_auto_settled_reply_and_auto_failed_reply(self):
        settled = {"challenger_site": 13, "opponent_site": 26,
                   "winner_site": 13, "loser_site": 26, "stake_raw": 10000,
                   "pot_display": 200.0, "digit": 2, "challenger_wins": True,
                   "digit_sum": 52,
                   "digit_expression": "2+0+2+6+0+9+2+4+1+5+3+8+2+2+2+2+2",
                   "winner_balance": 600.0, "loser_balance": 400.0,
                   "settled_time": "2026-09-24 15:38:22.222"}
        self.plugin.pk_handler.create_challenge = AsyncMock(
            return_value=("AUTO_SETTLED", settled))
        event = await self.event("PK 26 100")
        params = self.parse("PK", event, NewApiSuitePlugin.handle_pk_command)
        replies = await self.collect(self.plugin.handle_pk_command(event, **params))
        result = replies[0]
        plain = "".join(getattr(c, "text", "") for c in result.chain)
        self.assertIn("PK 结算", plain)
        self.assertIn("<@OPENID_13> <@OPENID_26> ", plain)
        self.assertIn("🧮 计算过程：2+0+2+6+0+9+2+4+1+5+3+8+2+2+2+2+2 = 52 → 尾数 2（双数）", plain)
        self.assertIn("单次下注上限：100 额度", plain)
        self.plugin.pk_handler.create_challenge = AsyncMock(
            return_value=("STAKE_TOO_LARGE", {"max": 100}))
        replies = await self.collect(self.plugin.handle_pk_command(event, **params))
        self.assertIn("单次下注不能超过 100 额度", replies[0])
        self.plugin.pk_handler.create_challenge = AsyncMock(
            return_value=("AUTO_ACCEPT_FAILED_REFUNDED", {"challenger_site": 13}))
        replies = await self.collect(self.plugin.handle_pk_command(event, **params))
        self.assertIn("已自动应战但未能完成结算", replies[0])

    async def test_pk_rank_lists_today(self):
        rows = [
            {"challenger_site": 13, "opponent_site": 26, "stake_raw": 10000,
             "status": "SETTLED", "winner_site": 13},
            {"challenger_site": 1, "opponent_site": 13, "stake_raw": 5000,
             "status": "SETTLED", "winner_site": 1},
            {"challenger_site": 13, "opponent_site": 1, "stake_raw": 2000,
             "status": "SETTLED", "winner_site": 13},
        ]
        self.plugin.core = SimpleNamespace(execute_query=AsyncMock(return_value=rows))
        event = await self.event("PK榜")
        event.is_at_or_wake_command = True
        command = CommandFilter("PK榜", alias={"pk榜", "PK盈亏", "pk盈亏"})
        command.init_handler_md(SimpleNamespace(handler=NewApiSuitePlugin.handle_pk_rank))
        self.assertTrue(command.filter(event, self.plugin.config))
        replies = await self.collect(self.plugin.handle_pk_rank(event))
        text = replies[0]
        self.assertIn("今日 PK 盈亏榜", text)
        self.assertIn("已结算 3 局", text)
        self.assertIn("参与玩家 3 人", text)
        self.assertIn("盈利 TOP3", text)
        self.assertIn("亏损 TOP3", text)
        self.assertLess(text.index("网站ID 13｜局数 3"), text.index("网站ID 1｜局数 2"))
        self.assertLess(text.index("网站ID 1｜局数 2"), text.index("网站ID 26｜局数 1"))
        self.assertIn("网站ID 13｜局数 3｜胜 2 负 1｜净 +70", text)
        self.assertIn("网站ID 1｜局数 2｜胜 1 负 1｜净 +30", text)
        self.assertIn("网站ID 26｜局数 1｜胜 0 负 1｜净 -100", text)

    async def test_pk_rank_empty_today(self):
        self.plugin.core = SimpleNamespace(execute_query=AsyncMock(return_value=[]))
        replies = await self.collect(self.plugin.handle_pk_rank(await self.event("PK榜")))
        self.assertIn("今天还没有已结算的 PK", replies[0])

    async def test_pk_history_lists_own_today(self):
        rows = [
            {"challenger_site": 13, "opponent_site": 26, "stake_raw": 10000,
             "status": "SETTLED", "winner_site": 13,
             "settled_at": "2026-09-25 03:00:00", "final_ms_digit": 4},
            {"challenger_site": 1, "opponent_site": 13, "stake_raw": 5000,
             "status": "SETTLED", "winner_site": 1,
             "settled_at": "2026-09-25 03:05:00", "final_ms_digit": 7},
        ]
        self.plugin.core = SimpleNamespace(
            get_user_by_identity=AsyncMock(return_value={"website_user_id": 13}),
            execute_query=AsyncMock(return_value=rows),
        )
        event = await self.event("PK战绩")
        event.is_at_or_wake_command = True
        command = CommandFilter("PK战绩", alias={"pk战绩"})
        command.init_handler_md(SimpleNamespace(handler=NewApiSuitePlugin.handle_pk_history))
        self.assertTrue(command.filter(event, self.plugin.config))
        replies = await self.collect(self.plugin.handle_pk_history(event))
        text = replies[0]
        self.assertIn("你的今日 PK 战绩", text)
        self.assertIn("局数 2｜胜 1 负 1｜净 +50", text)
        self.assertIn("赢局入账 100｜输局支出 50", text)
        self.assertIn("作为挑战者 1 局｜作为应战者 1 局", text)
        self.assertIn("对 网站ID 1｜押 50｜❌ 负｜尾数 7", text)
        self.assertIn("对 网站ID 26｜押 100｜✅ 胜｜尾数 4", text)

    async def test_pk_total_history_lists_all_records(self):
        rows = [
            {"challenger_site": 13, "opponent_site": 26, "stake_raw": 10000,
             "status": "SETTLED", "winner_site": 13,
             "settled_at": "2026-09-24 03:00:00", "final_ms_digit": 4},
            {"challenger_site": 1, "opponent_site": 13, "stake_raw": 5000,
             "status": "SETTLED", "winner_site": 1,
             "settled_at": "2026-09-25 03:05:00", "final_ms_digit": 7},
        ]
        self.plugin.core = SimpleNamespace(
            get_user_by_identity=AsyncMock(return_value={"website_user_id": 13}),
            execute_query=AsyncMock(return_value=rows),
        )
        event = await self.event("PK总战绩")
        event.is_at_or_wake_command = True
        command = CommandFilter("PK总战绩", alias={"pk总战绩"})
        command.init_handler_md(SimpleNamespace(handler=NewApiSuitePlugin.handle_pk_total_history))
        self.assertTrue(command.filter(event, self.plugin.config))
        replies = await self.collect(self.plugin.handle_pk_total_history(event))
        text = replies[0]
        self.assertIn("你的 PK 总战绩", text)
        self.assertIn("局数 2｜胜 1 负 1｜净 +50", text)
        self.assertIn("对 网站ID 1｜押 50｜❌ 负｜尾数 7", text)

    async def test_pk_history_query_other_by_admin(self):
        rows = [{"challenger_site": 26, "opponent_site": 13, "stake_raw": 8000,
                 "status": "SETTLED", "winner_site": 26,
                 "settled_at": "2026-09-25 04:00:00", "final_ms_digit": 2}]
        execute_query = AsyncMock(return_value=rows)
        self.plugin.core = SimpleNamespace(
            get_user_by_identity=AsyncMock(return_value={"website_user_id": 13}),
            execute_query=execute_query,
        )
        event = await self.event("PK战绩 26")
        event.role = "admin"
        replies = await self.collect(self.plugin.handle_pk_history(event, "26"))
        self.assertIn("网站ID 26", replies[0])
        self.assertEqual(execute_query.await_count, 1)

    async def test_pk_history_other_id_denied_for_member(self):
        self.plugin.core = SimpleNamespace(
            get_user_by_identity=AsyncMock(return_value={"website_user_id": 13}),
            execute_query=AsyncMock(),
        )
        event = await self.event("PK战绩 26")
        replies = await self.collect(self.plugin.handle_pk_history(event, "26"))
        self.assertIn("只有管理员", replies[0])
        self.plugin.core.execute_query.assert_not_awaited()

    async def test_pk_history_empty_today(self):
        self.plugin.core = SimpleNamespace(
            get_user_by_identity=AsyncMock(return_value={"website_user_id": 13}),
            execute_query=AsyncMock(return_value=[]),
        )
        replies = await self.collect(self.plugin.handle_pk_history(await self.event("PK战绩")))
        self.assertIn("今天还没有 PK 记录", replies[0])

    async def test_pk_command_usage_without_amount(self):
        event = await self.event("PK 26")
        params = self.parse("PK", event, NewApiSuitePlugin.handle_pk_command)
        replies = await self.collect(self.plugin.handle_pk_command(event, **params))
        self.assertIn("用法", replies[0])
        self.assertIn("单次下注上限：100 额度", replies[0])
        self.plugin.pk_handler.create_challenge.assert_not_awaited()

    async def test_accept_pk_with_and_without_identifier(self):
        for text, expected in (("接受PK 13", "13"), ("接受PK", None)):
            with self.subTest(text=text):
                event = await self.event(text)
                params = self.parse("接受PK", event, NewApiSuitePlugin.handle_accept_pk)
                replies = await self.collect(self.plugin.handle_accept_pk(event, **params))
                self.plugin.pk_handler.accept_challenge.assert_awaited_with(
                    "openid:" + SENDER, expected)
                result = replies[0]
                self.assertEqual([c for c in result.chain if isinstance(c, At)], [])
                plain = "".join(getattr(c, "text", "") for c in result.chain)
                self.assertIn("<@OPENID_13> <@OPENID_26> ", plain)
                self.assertIn("PK 结算", plain)
                self.assertIn("结算毫秒时间戳：2026-09-24 15:38:22.222", plain)
                self.assertIn("🧮 计算过程：2+0+2+6+0+9+2+4+1+5+3+8+2+2+2+2+2 = 52 → 尾数 2（双数）", plain)
                self.assertIn("单次下注上限：100 额度", plain)
                self.assertIn("网站ID 13（胜）→ 600", plain)
                self.assertIn("网站ID 26（负）→ 400", plain)

    async def test_wild_platform_uses_at_components(self):
        event = await self.event("接受PK 13")
        with patch.object(event, "get_platform_name", return_value="aiocqhttp"):
            self.plugin.core.get_user_by_website_id = AsyncMock(
                side_effect=lambda site: {"qq_id": 70000 + site})
            params = self.parse("接受PK", event, NewApiSuitePlugin.handle_accept_pk)
            replies = await self.collect(self.plugin.handle_accept_pk(event, **params))
        result = replies[0]
        ats = [str(c.qq) for c in result.chain if isinstance(c, At)]
        self.assertEqual(ats, ["70013", "70026"])
        plain = "".join(getattr(c, "text", "") for c in result.chain)
        self.assertIn("PK 结算", plain)


if __name__ == "__main__":
    unittest.main()
