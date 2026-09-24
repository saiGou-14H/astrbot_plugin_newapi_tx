# pk_logic.py

import asyncio
import math
import time
from datetime import datetime, timedelta
from typing import Dict, Tuple, Any, Optional

from astrbot.api import logger, AstrBotConfig

from .config_utils import config_get
from .newapi_utils import NewApiCore


class PkLogic:
    """PK 挑战：挑战者押注、对方 5 分钟内应战、按结算毫秒尾数奇偶定胜负。

    资金模型（公开、确定性）：
      - 发起挑战时挑战者立即扣除 stake；
      - 应战时应战者立即扣除同等 stake；
      - 结算毫秒时间戳全部数字求和取尾数：双数 → 挑战者胜；单数 → 应战者胜；
      - 胜者获得双方押注之和（本金返还 + 对方押注），败者失去押注；
      - 挑战 5 分钟过期：超时自动退还挑战者押注；
      - 任何扣款/发放失败路径都会尽力原路退款，并在无法退款时留下需人工介入的状态。
    """

    # 最终状态（数据库）
    ST_PENDING = "PENDING"          # 等待应战
    ST_ACCEPTING = "ACCEPTING"      # 已被应战锁claim，结算中
    ST_SETTLED = "SETTLED"          # 已正常结算
    ST_REFUNDED = "REFUNDED"        # 结算异常但双方押金已全部退还
    ST_EXPIRED = "EXPIRED"          # 过期，押金已退还
    ST_EXPIRED_UNREFUNDED = "EXPIRED_UNREFUNDED"      # 过期但退款失败
    ST_SETTLE_UNREFUNDED = "SETTLE_UNREFUNDED"        # 结算异常且退款未完全成功

    def __init__(self, config: AstrBotConfig, core: NewApiCore):
        self.config = config
        self.core = core
        # 并发锁：按参与双方（site 排序）串行化挑战/应战/结算与退款，杜绝 TOCTOU
        self._pair_locks: Dict[str, asyncio.Lock] = {}
        # 可注入时钟（秒），测试用；生产为真实时间
        self._now_fn = time.time
        logger.info("[PkLogic] Initialized.")

    # ------------------------------------------------------------------ #
    #  工具                                                             #
    # ------------------------------------------------------------------ #

    def _now(self) -> datetime:
        return datetime.utcfromtimestamp(self._now_fn())

    def _expires_at(self) -> str:
        seconds = int(config_get(self.config, 'pk_settings.expiry_seconds', 300) or 300)
        if seconds < 30 or seconds > 86400:
            seconds = 300
        return self._format_dt(self._now() + timedelta(seconds=seconds))

    @staticmethod
    def _format_dt(value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _parse_dt(value) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _pair_key(site_a, site_b) -> str:
        left, right = sorted((str(site_a), str(site_b)))
        return f"{left}|{right}"

    def _get_pair_lock(self, site_a, site_b) -> asyncio.Lock:
        key = self._pair_key(site_a, site_b)
        if key not in self._pair_locks:
            self._pair_locks[key] = asyncio.Lock()
        return self._pair_locks[key]

    def _raw_amount(self, display_amount: float) -> Tuple[Optional[int], str]:
        """把显示额度换算为原始额度；返回 (raw, 错误码)。"""
        try:
            display = float(display_amount)
        except (TypeError, ValueError):
            return None, "INVALID_AMOUNT"
        if not math.isfinite(display) or display <= 0:
            return None, "INVALID_AMOUNT"
        ratio = config_get(self.config, 'binding_settings.quota_display_ratio', 500000)
        if not isinstance(ratio, (int, float)) or not math.isfinite(ratio) or ratio <= 0:
            return None, "INVALID_AMOUNT"
        raw = display * ratio
        if not math.isfinite(raw) or raw < 1 or raw > 2**63 - 1:
            return None, "INVALID_AMOUNT"
        return int(round(raw)), "VALID"

    @staticmethod
    def _timestamp_digit(timestamp_str: str) -> int:
        """时间戳所有数字求和后取尾数（个位）。"""
        total = sum(int(ch) for ch in timestamp_str if ch.isdigit())
        return total % 10

    async def _refund(self, site_id: int, raw: int, reason: str) -> bool:
        ok = await self.core.manage_user_quota(site_id, "add", raw)
        if not ok:
            logger.critical(
                f"[PK] 退款失败需人工介入 site={site_id} raw={raw} reason={reason}"
            )
        return ok

    # ------------------------------------------------------------------ #
    #  数据库操作                                                       #
    # ------------------------------------------------------------------ #

    async def _insert_match(self, challenger_site: int, opponent_site: int,
                            stake_raw: int, expires_at: str) -> Optional[int]:
        row_id = await self.core.execute_query(
            "INSERT INTO newapi_pk_matches "
            "(challenger_site, opponent_site, stake_raw, status, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (challenger_site, opponent_site, stake_raw, self.ST_PENDING,
             self._format_dt(self._now()), expires_at),
            return_lastrowid=True,
        )
        return int(row_id) if row_id else None

    async def _pending_between(self, site_a: int, site_b: int) -> Optional[int]:
        row = await self.core.execute_query(
            "SELECT id FROM newapi_pk_matches "
            "WHERE status = %s AND challenger_site IN (%s, %s) AND opponent_site IN (%s, %s) "
            "ORDER BY id LIMIT 1",
            (self.ST_PENDING, site_a, site_b, site_a, site_b), fetch='one',
        )
        return int(row['id']) if row else None

    async def _pending_for_opponent(self, opponent_site: int,
                                    challenger_site: Optional[int] = None) -> list:
        if challenger_site is not None:
            return await self.core.execute_query(
                "SELECT id, challenger_site, stake_raw, expires_at FROM newapi_pk_matches "
                "WHERE status = %s AND opponent_site = %s AND challenger_site = %s "
                "ORDER BY id",
                (self.ST_PENDING, opponent_site, challenger_site), fetch='all',
            ) or []
        return await self.core.execute_query(
            "SELECT id, challenger_site, stake_raw, expires_at FROM newapi_pk_matches "
            "WHERE status = %s AND opponent_site = %s ORDER BY id",
            (self.ST_PENDING, opponent_site), fetch='all',
        ) or []

    async def _get_match(self, match_id: int) -> Optional[Dict]:
        return await self.core.execute_query(
            "SELECT * FROM newapi_pk_matches WHERE id = %s", (match_id,), fetch='one'
        )

    # ------------------------------------------------------------------ #
    #  过期处理                                                         #
    # ------------------------------------------------------------------ #

    async def sweep_expired(self) -> list:
        """把已过期的 PENDING 挑战翻转为 EXPIRED 并退还挑战者押金。

        先原子翻转状态，再退款；退款失败标记 EXPIRED_UNREFUNDED 留待人工。
        """
        now = self._now()
        rows = await self.core.execute_query(
            "SELECT id, challenger_site, stake_raw, expires_at FROM newapi_pk_matches "
            "WHERE status = %s", (self.ST_PENDING,), fetch='all',
        ) or []
        expired_ids = []
        for row in rows:
            expires = self._parse_dt(row.get('expires_at'))
            if expires is None or expires > now:
                continue
            flipped = await self.core.execute_query(
                "UPDATE newapi_pk_matches SET status = %s WHERE id = %s AND status = %s",
                (self.ST_EXPIRED, row['id'], self.ST_PENDING),
            )
            if not flipped:
                continue
            if not await self._refund(row['challenger_site'], row['stake_raw'], 'expired'):
                await self.core.execute_query(
                    "UPDATE newapi_pk_matches SET status = %s WHERE id = %s AND status = %s",
                    (self.ST_EXPIRED_UNREFUNDED, row['id'], self.ST_EXPIRED),
                )
            expired_ids.append(int(row['id']))
        return expired_ids

    # ------------------------------------------------------------------ #
    #  主流程                                                           #
    # ------------------------------------------------------------------ #

    async def create_challenge(self, challenger_identity, target_identifier,
                               display_amount) -> Tuple[str, Dict[str, Any]]:
        """发起挑战：校验 → 锁内余额复核 → 扣款 → 落库（失败退款）。"""
        if not config_get(self.config, 'pk_settings.enabled', True):
            return "DISABLED", {}
        raw, code = self._raw_amount(display_amount)
        if code != "VALID":
            return "INVALID_AMOUNT", {}
        max_stake = float(config_get(self.config, 'pk_settings.max_stake', 100) or 0)
        if max_stake > 0 and float(display_amount) > max_stake:
            return "STAKE_TOO_LARGE", {"max": max_stake}

        challenger_binding = await self.core.get_user_by_identity(challenger_identity)
        if not challenger_binding:
            return "CHALLENGER_NOT_BOUND", {}
        id_type, target_binding = await self.core.lookup_binding(target_identifier)
        if id_type == "NOT_FOUND" or not target_binding:
            return "TARGET_NOT_FOUND", {"id": target_identifier}
        challenger_site = challenger_binding['website_user_id']
        opponent_site = target_binding['website_user_id']
        if challenger_site == opponent_site:
            return "CANNOT_PK_SELF", {}

        async with self._get_pair_lock(challenger_site, opponent_site):
            await self.sweep_expired()
            if await self._pending_between(challenger_site, opponent_site):
                return "ALREADY_PENDING", {}
            # 锁内余额复核 + 扣款（防并发双花）
            fresh = await self.core.get_api_user_data(challenger_site)
            balance_raw = int(fresh.get("quota", 0) or 0) if fresh else 0
            ratio = config_get(self.config, 'binding_settings.quota_display_ratio', 500000)
            if raw > balance_raw:
                return "INSUFFICIENT_BALANCE", {
                    "need": raw / ratio, "balance": balance_raw / ratio,
                }
            if not await self.core.manage_user_quota(challenger_site, "subtract", raw):
                return "DEDUCT_FAILED", {}
            match_id = await self._insert_match(challenger_site, opponent_site, raw, self._expires_at())
            if match_id is None:
                await self._refund(challenger_site, raw, 'insert_failed')
                return "DB_FAILED", {}
        auto_site = int(config_get(self.config, 'pk_settings.auto_accept_admin_site', 0) or 0)
        if auto_site > 0 and opponent_site == auto_site:
            return await self._auto_accept(match_id, challenger_site, opponent_site, raw)
        return "CREATED", {
            "match_id": match_id,
            "challenger_site": challenger_site,
            "opponent_site": opponent_site,
            "stake_raw": raw,
            "stake_display": raw / ratio,
        }

    async def _auto_accept(self, match_id: int, challenger_site: int,
                           opponent_site: int, stake_raw: int) -> Tuple[str, Dict[str, Any]]:
        """管理员账号自动应战：claim 后立即结算；失败则退还挑战者押注。"""
        claimed = await self.core.execute_query(
            "UPDATE newapi_pk_matches SET status = %s WHERE id = %s AND status = %s",
            (self.ST_ACCEPTING, match_id, self.ST_PENDING),
        )
        if not claimed:
            await self._refund(challenger_site, stake_raw, 'auto_claim_failed')
            return "AUTO_ACCEPT_FAILED_REFUNDED", {
                "challenger_site": challenger_site, "stake_raw": stake_raw,
            }
        try:
            status, details = await self._settle(match_id, challenger_site, opponent_site, stake_raw)
        except BaseException:
            status, details = await self._fail_settle(match_id, challenger_site, opponent_site, stake_raw)
        if status in ("ACCEPT_INSUFFICIENT_BALANCE", "ACCEPT_DEDUCT_FAILED"):
            # 管理员余额不足或扣款失败：挑战不成立，退还挑战者押注
            await self._refund(challenger_site, stake_raw, 'auto_accept_failed')
            await self.core.execute_query(
                "UPDATE newapi_pk_matches SET status = %s, settled_at = %s "
                "WHERE id = %s AND status = %s",
                (self.ST_REFUNDED, self._format_dt(self._now()), match_id, self.ST_PENDING),
            )
            return "AUTO_ACCEPT_FAILED_REFUNDED", {
                "challenger_site": challenger_site, "opponent_site": opponent_site,
                "stake_raw": stake_raw,
            }
        if status == "SETTLED":
            return "AUTO_SETTLED", details
        return status, details

    async def accept_challenge(self, opponent_identity,
                               challenger_identifier: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        """应战并结算：claim → 余额复核扣款 → 毫秒尾数判胜负 → 发放/退款。"""
        if not config_get(self.config, 'pk_settings.enabled', True):
            return "DISABLED", {}
        opponent_binding = await self.core.get_user_by_identity(opponent_identity)
        if not opponent_binding:
            return "OPPONENT_NOT_BOUND", {}
        opponent_site = opponent_binding['website_user_id']

        challenger_site = None
        if challenger_identifier:
            _, binding = await self.core.lookup_binding(challenger_identifier)
            if not binding:
                return "NOT_FOUND", {}
            challenger_site = binding['website_user_id']

        async with self._get_pair_lock(opponent_site, challenger_site or opponent_site):
            expired = await self.sweep_expired()
            if expired and challenger_site is not None:
                # 指定了发起人且其挑战已过期
                pass
            pending = await self._pending_for_opponent(opponent_site, challenger_site)
            now = self._now()
            live = [row for row in pending
                    if (expires := self._parse_dt(row.get('expires_at'))) and expires > now]
            if not live:
                if expired:
                    return "EXPIRED", {}
                return "NOT_FOUND", {}
            if len(live) > 1:
                return "MULTIPLE_PENDING", {"count": len(live)}
            row = live[0]
            match_id = int(row['id'])
            challenger_site = int(row['challenger_site'])
            stake_raw = int(row['stake_raw'])

            # 原子 claim：并发应战只有一方成功
            claimed = await self.core.execute_query(
                "UPDATE newapi_pk_matches SET status = %s WHERE id = %s AND status = %s",
                (self.ST_ACCEPTING, match_id, self.ST_PENDING),
            )
            if not claimed:
                return "NOT_FOUND", {}
            try:
                return await self._settle(match_id, challenger_site, opponent_site, stake_raw)
            except BaseException:
                return await self._fail_settle(match_id, challenger_site, opponent_site, stake_raw)

    async def _settle(self, match_id: int, challenger_site: int, opponent_site: int,
                      stake_raw: int) -> Tuple[str, Dict[str, Any]]:
        ratio = config_get(self.config, 'binding_settings.quota_display_ratio', 500000)
        fresh = await self.core.get_api_user_data(opponent_site)
        balance_raw = int(fresh.get("quota", 0) or 0) if fresh else 0
        if stake_raw > balance_raw:
            await self.core.execute_query(
                "UPDATE newapi_pk_matches SET status = %s WHERE id = %s AND status = %s",
                (self.ST_PENDING, match_id, self.ST_ACCEPTING),
            )
            return "ACCEPT_INSUFFICIENT_BALANCE", {
                "need": stake_raw / ratio, "balance": balance_raw / ratio,
            }
        if not await self.core.manage_user_quota(opponent_site, "subtract", stake_raw):
            await self.core.execute_query(
                "UPDATE newapi_pk_matches SET status = %s WHERE id = %s AND status = %s",
                (self.ST_PENDING, match_id, self.ST_ACCEPTING),
            )
            return "ACCEPT_DEDUCT_FAILED", {}

        settled_time = datetime.fromtimestamp(self._now_fn()).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        digit_parts = [ch for ch in settled_time if ch.isdigit()]
        digit_sum = sum(int(ch) for ch in digit_parts)
        digit = digit_sum % 10
        challenger_wins = digit % 2 == 0  # 双数=挑战者胜，单数=应战者胜
        winner_site = challenger_site if challenger_wins else opponent_site
        loser_site = opponent_site if challenger_wins else challenger_site
        pot_raw = stake_raw * 2

        if not await self.core.manage_user_quota(winner_site, "add", pot_raw):
            return await self._fail_settle(match_id, challenger_site, opponent_site, stake_raw)

        await self.core.execute_query(
            "UPDATE newapi_pk_matches SET status = %s, settled_at = %s, "
            "winner_site = %s, final_ms_digit = %s WHERE id = %s AND status = %s",
            (self.ST_SETTLED, self._format_dt(self._now()), winner_site, digit,
             match_id, self.ST_ACCEPTING),
        )
        winner_data = await self.core.get_api_user_data(winner_site)
        loser_data = await self.core.get_api_user_data(loser_site)
        winner_balance = (winner_data.get("quota") / ratio) if winner_data else None
        loser_balance = (loser_data.get("quota") / ratio) if loser_data else None
        return "SETTLED", {
            "match_id": match_id,
            "challenger_site": challenger_site,
            "opponent_site": opponent_site,
            "winner_site": winner_site,
            "loser_site": loser_site,
            "stake_raw": stake_raw,
            "stake_display": stake_raw / ratio,
            "pot_display": pot_raw / ratio,
            "digit": digit,
            "digit_sum": digit_sum,
            "digit_expression": "+".join(digit_parts),
            "challenger_wins": challenger_wins,
            "winner_balance": winner_balance,
            "loser_balance": loser_balance,
            "settled_time": settled_time,
        }

    async def _fail_settle(self, match_id: int, challenger_site: int,
                           opponent_site: int, stake_raw: int) -> Tuple[str, Dict[str, Any]]:
        """结算异常兜底：退还双方押金，记录状态；任何退款失败都需人工介入。"""
        challenger_ok = await self._refund(challenger_site, stake_raw, 'settle_failed')
        opponent_ok = await self._refund(opponent_site, stake_raw, 'settle_failed')
        status = self.ST_REFUNDED if (challenger_ok and opponent_ok) else self.ST_SETTLE_UNREFUNDED
        await self.core.execute_query(
            "UPDATE newapi_pk_matches SET status = %s, settled_at = %s "
            "WHERE id = %s AND status = %s",
            (status, self._format_dt(self._now()), match_id, self.ST_ACCEPTING),
        )
        if status == self.ST_REFUNDED:
            logger.error(f"[PK] 结算失败但双方押金已全部退还 match={match_id}")
        return "SETTLE_FAILED_REFUNDED", {"match_id": match_id}
