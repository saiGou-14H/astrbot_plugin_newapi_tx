import os
import math
from astrbot.core.star.filter.command import GreedyStr
import re
import asyncio
from typing import Any, Dict, Optional, Tuple
from functools import wraps
from datetime import datetime, timedelta
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.api.message_components import At

from .newapi_utils import NewApiCore
from .heist_logic import HeistLogic
from .i18n import translate
from .config_utils import config_get
from .qq_mentions import official_mention_ids
from .qq_compat import QQMentionCompat, mention_summary, query_group_receive_mode
from .pk_logic import PkLogic

def load_plugin_version() -> str:
    """
    从插件根目录的 metadata.yaml 读取 version 字段，
    作为本插件的唯一版本来源，避免主代码中硬编码版本号。
    """
    try:
        metadata_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "metadata.yaml"
        )
        with open(metadata_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("version:"):
                    version = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    if version:
                        return version
    except Exception as e:
        logger.warning(f"读取 metadata.yaml 版本号失败，使用默认版本: {e}")
    return "1.0.4"

PLUGIN_VERSION = load_plugin_version()

# 「野机优先」让行窗口（秒）：签到同时经官机(OpenID)与野机(QQ号)触发时，
# 官机侧延迟该时长再抢签到锁，让野机请求稳定先手；仅官机单独触发时仅多等这一小段。
_WILD_PRIORITY_YIELD_SECONDS = 1.5

def require_binding(f):
    """
    检查命令发起者是否绑定网站ID，若未绑定则中断并提示，若已绑定则附加binding对象以便后续使用。
    同时支持 QQ 号绑定（int）与 OpenID 绑定（str）。
    """
    @wraps(f)
    async def wrapper(self, event: AstrMessageEvent, *args, **kwargs):
        sender_id = event.get_sender_id()
        
        # 避免重复获取binding
        if hasattr(event, 'binding'):
            async for item in f(self, event, *args, **kwargs):
                yield item
            return

        binding = await self.core.get_user_by_identity(self._sender_identity(event))

        if not binding:
            yield self._reply(event, self.t("not_bound"))
            return
        
        # 附加binding对象到event
        event.binding = binding
        
        async for item in f(self, event, *args, **kwargs):
            yield item
            
    return wrapper

def require_group_whitelist(f):
    """
    仅当消息来自白名单群（或白名单功能未启用）时放行，否则静默忽略、不回复。

    用于「签到 / 打劫 / 查询余额」等仅允许在配置群内响应的命令。
    """
    @wraps(f)
    async def wrapper(self, event: AstrMessageEvent, *args, **kwargs):
        if not self._command_group_allowed(event):
            return
        async for item in f(self, event, *args, **kwargs):
            yield item
            
    return wrapper

class TargetSelectionError(ValueError):
    """A user supplied more than one target for a single-account operation."""


def guard_errors(f):
    """全局异常护栏：捕获命令处理中的未预期异常，记录完整堆栈，
    并向用户回复人类可读提示（附带 issue 反馈链接），避免框架直接抛出难懂的错误转储。"""
    @wraps(f)
    async def wrapper(self, event: AstrMessageEvent, *args, **kwargs):
        try:
            async for item in f(self, event, *args, **kwargs):
                yield item
        except TargetSelectionError as e:
            yield self._reply(event, str(e))
        except Exception as e:
            logger.error(f"[NewAPI] 处理命令 {getattr(f, '__name__', '?')} 时发生未预期异常: {e}", exc_info=True)
            try:
                yield self._reply(event, self.t("common.unexpected_error", err=str(e)))
            except Exception:
                pass
    return wrapper

@register(
    "NewAPI_plugin",
    "Future-404",
    "集成了核心用户管理与娱乐功能的New API插件套件。",
    PLUGIN_VERSION
)
class NewApiSuitePlugin(Star):
    """
    New API 功能套件主插件类，作为功能套件的唯一入口点。
    """
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.core = NewApiCore(config)
        self.heist_handler = HeistLogic(config, self.core)
        self.pk_handler = PkLogic(config, self.core)
        # 回复语言：zh / en（配置 i18n_settings.language）
        self.lang = self._resolve_language()
        # 余额缓存：用户每次操作时顺手更新，排行榜直接读缓存无需查 API
        self._balance_cache: dict[int, tuple[int, int]] = {}
        # 「红包仅官机」门控判定环形记录（最近20条），供「红包诊断」指令查看
        self._rp_gate_log: list = []
        # KV 绑定缓存读-改-写锁，避免并发操作互相覆盖丢失更新
        self._kv_lock = asyncio.Lock()
        logger.info("[NewAPI Suite] 插件已实例化，准备进行异步初始化...")
        # 启动即打印红包门控关键状态，便于确认运行中的代码与配置
        try:
            _rp_conf = config_get(self.config, 'red_packet_settings', {}) or {}
            logger.info(
                "[NewAPI Suite] 红包仅官机 official_only=%s | 官机Markdown=%s | 版本=%s",
                _rp_conf.get('official_only', False),
                (config_get(self.config, 'reply_settings', {}) or {}).get('official_markdown', True),
                PLUGIN_VERSION,
            )
        except Exception as e:
            logger.warning(f"[NewAPI Suite] 启动状态打印失败: {e}")

    def _resolve_language(self) -> str:
        """解析回复语言，缺省中文。"""
        lang = config_get(self.config, 'i18n_settings.language', 'zh')
        return "en" if str(lang).lower().startswith("en") else "zh"

    def t(self, key: str, **kwargs) -> str:
        """翻译运行时消息。"""
        return translate(self.lang, key, **kwargs)

    def _is_debug(self) -> bool:
        """是否开启调试模式（debug_settings.enabled），动态读取便于随时切换。"""
        return bool(config_get(self.config, 'debug_settings.enabled', False))

    def _red_packet_official_only(self) -> bool:
        """红包是否仅限官机（red_packet_settings.official_only），动态读取便于随时切换。"""
        try:
            conf = config_get(self.config, 'red_packet_settings', {}) or {}
            return bool(conf.get('official_only', False))
        except Exception:
            return False

    def _is_wild_bot_sender(self, event: AstrMessageEvent) -> bool:
        """优先采用平台信息；官方 OpenID 即使全为数字也不是 QQ 号。"""
        platform = getattr(event, "get_platform_name", lambda: "")()
        if platform == "qq_official" or str(event.get_self_id()) == "qq_official":
            return False
        sender = str(event.get_sender_id() or "").strip()
        return sender.isascii() and sender.isdecimal() and len(sender) < 32

    def _sender_identity(self, event: AstrMessageEvent) -> str:
        kind = "qq" if self._is_wild_bot_sender(event) else "openid"
        return f"{kind}:{str(event.get_sender_id()).strip()}"

    def _sender_display(self, event: AstrMessageEvent) -> str:
        """领取人展示名：优先群消息自带昵称，回退到身份字符串。"""
        try:
            mo = getattr(event, 'message_obj', None)
            s = getattr(mo, 'sender', None)
            n = getattr(s, 'nickname', None) or getattr(s, 'display_name', None)
            if n and str(n).strip():
                return str(n)
        except Exception:
            pass
        return str(event.get_sender_id())

    # --- 回复输出 ---

    def _maybe_markdown(self, event: AstrMessageEvent, text: str) -> str:
        """「官机 Markdown」：来自官方机器人(OpenID 身份)的请求，把回复转为
        QQ 原生 Markdown 友好格式（换行符转 \\r）；野机与关闭开关时保持纯文本。
        官方机器人若无原生 Markdown 权限，AstrBot 框架会自动回退普通文本发送。"""
        if not isinstance(text, str) or not text:
            return text
        if not config_get(self.config, 'reply_settings.official_markdown', True):
            return text
        try:
            sender = event.get_sender_id()
        except Exception:
            return text
        if sender is None or self._is_wild_bot_sender(event):
            return text
        return text.replace("\n", "\r")

    def _reply(self, event: AstrMessageEvent, text):
        """统一回复出口：所有用户可见回复经此发出，便于按来源套用格式。"""
        return event.plain_result(self._maybe_markdown(event, text))

    @staticmethod
    def _fmt_quota(value) -> str:
        """把显示额度格式化为最多 6 位小数的紧凑字符串。"""
        try:
            return f"{float(value):.6f}".rstrip('0').rstrip('.') or "0"
        except (TypeError, ValueError):
            return str(value)

    def _pk_settled_reply(self, details) -> str:
        """组装 PK 结算文案（含毫秒时间戳、计算过程与双方余额）。"""
        parity = self.t("pk.parity_odd" if int(details['digit']) % 2 == 1 else "pk.parity_even")
        winner_balance = details.get('winner_balance')
        loser_balance = details.get('loser_balance')
        return self.t("pk.settled", digit=details['digit'], parity=parity,
                      time=details.get('settled_time', ''),
                      expression=details.get('digit_expression', ''),
                      digit_sum=details.get('digit_sum', ''),
                      winner=details['winner_site'], loser=details['loser_site'],
                      pot=self._fmt_quota(details['pot_display']),
                      winner_balance=(self._fmt_quota(winner_balance)
                                      if winner_balance is not None
                                      else self.t("pk.balance_unavailable")),
                      loser_balance=(self._fmt_quota(loser_balance)
                                     if loser_balance is not None
                                     else self.t("pk.balance_unavailable"))) \
            + (self._pk_max_hint() or "")

    def _pk_max_hint(self) -> str:
        """下注上限提示；配置为 0（不限）时返回空串。"""
        max_stake = float(config_get(self.config, 'pk_settings.max_stake', 100) or 0)
        if max_stake <= 0:
            return ""
        return "\n" + self.t("pk.max_hint", max=self._fmt_quota(max_stake))

    async def _pk_at_identities(self, event: AstrMessageEvent, sites) -> list:
        """按平台取网站 ID 对应的可 @身份：官机只取 OpenID，野机只取 QQ 号。"""
        official = event.get_platform_name() in ("qq_official", "qq_official_webhook")
        identities = []
        for site in sites:
            row = (await self.core.get_openid_by_website_id(site)) if official \
                else (await self.core.get_user_by_website_id(site))
            identity = (row or {}).get('openid' if official else 'qq_id')
            if identity:
                value = str(identity).strip()
                if value and value not in identities:
                    identities.append(value)
        return identities

    def _reply_with_ats(self, event: AstrMessageEvent, ats, text):
        """带 @的消息回复；无 @时回退普通文本回复。

        官方 QQ 适配器发送时会丢弃 At 消息段，因此官方通道直接把提及写成
        协议正文语法 <@openid>，并以纯文本模式发送；野机通道继续用 At 组件。
        """
        if not ats:
            return self._reply(event, text)
        from astrbot.api.message_components import Plain
        from astrbot.core.message.message_event_result import MessageChain, MessageEventResult
        platform = event.get_platform_name()
        if platform in ("qq_official", "qq_official_webhook"):
            text = "".join(f"<@{identity}> " for identity in ats) + text
            chain = MessageChain(chain=[Plain(text=text)], use_markdown_=False)
            result = MessageEventResult(chain=chain.chain, use_markdown_=False)
            event.set_result(result)
            return result
        chain = MessageChain()
        for identity in ats:
            chain.at("", qq=identity)
            chain.message(" ")
        chain.message(self._maybe_markdown(event, text))
        result = MessageEventResult(chain=chain.chain)
        event.set_result(result)
        return result

    def _red_packet_official_only_blocked(self, event: AstrMessageEvent, cmd: str = "") -> bool:
        """「红包仅官机」开启且本次请求来自野机（数字 QQ 身份）时返回 True，调用方应拒绝处理。

        判定规则：触发者身份为数字(QQ) → 野机 → 拦截；非数字(OpenID) → 官机 → 放行。
        同时把每次判定写入内存环形记录，供「红包诊断」指令直接在对话内查看。
        """
        on = self._red_packet_official_only()
        wild = self._is_wild_bot_sender(event)
        # 记录最近判定（含开关关闭时的放行），便于「红包诊断」排查部署问题
        try:
            self._rp_gate_log.append({
                "time": (datetime.utcnow() + timedelta(
                    hours=float(config_get(self.config, 'check_in_settings.timezone_offset_hours', 0) or 0)
                )).strftime("%m-%d %H:%M:%S"),
                "cmd": cmd,
                "sender": event.get_sender_id(),
                "wild": wild,
                "on": on,
                "blocked": bool(on and wild),
            })
            if len(self._rp_gate_log) > 20:
                del self._rp_gate_log[:-20]
        except Exception as e:
            logger.warning(f"[红包诊断] 判定记录失败: {e}")
        if on and self._is_debug():
            logger.info(
                f"[红包仅官机][DEBUG] 开关={on} 触发者={event.get_sender_id()!r} "
                f"type={type(event.get_sender_id()).__name__} 野机判定={wild} → {'拦截(静默)' if wild else '放行'}"
            )
        if on and wild:
            logger.info(f"[红包仅官机] 已静默忽略野机请求 sender={event.get_sender_id()!r}")
        return on and wild

    @filter.command("红包诊断")
    @guard_errors
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_rp_diag(self, event: AstrMessageEvent):
        """(管理员) 在对话内查看「红包仅官机」门控状态与最近红包指令的判定记录。"""
        conf = config_get(self.config, 'red_packet_settings', {}) or {}
        reply_conf = config_get(self.config, 'reply_settings', {}) or {}
        sender = event.get_sender_id()
        wild = self._is_wild_bot_sender(event)

        toggle = self.t("rp.diag.toggle_on") if conf.get('official_only', False) else self.t("rp.diag.toggle_off")
        md_state = self.t("rp.diag.toggle_on") if reply_conf.get('official_markdown', True) else self.t("rp.diag.toggle_off")
        identity = (
            self.t("rp.diag.identity_wild", sender=str(sender))
            if wild else self.t("rp.diag.identity_official", sender=str(sender))
        )

        records = list(getattr(self, "_rp_gate_log", []))[-10:]
        if records:
            lines = [self.t(
                "rp.diag.record",
                time=r["time"], cmd=r["cmd"] or "-",
                sender=str(r["sender"]),
                kind=self.t("rp.diag.kind_wild") if r["wild"] else self.t("rp.diag.kind_official"),
                verdict=(self.t("rp.diag.verdict_block") if r["blocked"] else self.t("rp.diag.verdict_pass")),
            ) for r in records]
            records_text = "\n".join(lines)
        else:
            records_text = self.t("rp.diag.no_records")

        yield self._reply(event, self.t(
            "rp.diag.header",
            version=PLUGIN_VERSION, toggle=toggle, md=md_state,
            identity=identity, records=records_text,
        ))

    def _command_group_allowed(self, event: AstrMessageEvent) -> bool:
        """判断当前消息是否命中群白名单：白名单未启用时一律放行。

        启用后，仅当消息来自 group_whitelist_settings.group_list 中列出的群时返回 True；
        私聊（group_id 为空）与未列出的群一律返回 False，实现「只监听配置群」。
        """
        conf = config_get(self.config, 'group_whitelist_settings', {})
        if not conf.get('enabled', False):
            return True
        group_list = conf.get('group_list', [])
        allowed = {str(g) for g in group_list if str(g).strip()}
        group_id = str(event.get_group_id() or "")
        return group_id in allowed

    async def _update_binding_cache(self, website_user_id, qq_id: Optional[int]):
        """更新 KV 绑定缓存单条映射（qq_id 为 None 表示删除），加锁避免并发读改写竞态。"""
        async with self._kv_lock:
            cache = await self.get_kv_data("binding_cache", {})
            if qq_id is None:
                cache.pop(str(website_user_id), None)
            else:
                cache[str(website_user_id)] = qq_id
            await self.put_kv_data("binding_cache", cache)

    def _extract_at_targets(self, event: AstrMessageEvent) -> list[str]:
        """明确的 @ 是平台身份，不能降级成同数字的网站 ID。"""
        self_id = str(event.get_self_id() or "").strip()
        kind = "qq" if self._is_wild_bot_sender(event) else "openid"
        targets = []
        for seg in event.get_messages():
            if not isinstance(seg, At):
                continue
            value = str(seg.qq or "").strip()
            if not value or value in (self_id, "qq_official", "all", "0"):
                continue
            target = f"{kind}:{value}"
            if target not in targets:
                targets.append(target)
        if getattr(event, "get_platform_name", lambda: "")() == "qq_official":
            # QQ 群适配器保留原始 mentions，但只为机器人自身生成 At。
            raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
            recovered = official_mention_ids(raw, self_id)
            for identity in recovered:
                target = f"openid:{identity}"
                if target not in targets:
                    targets.append(target)
        return targets

    def _extract_at_qq(self, event: AstrMessageEvent) -> Optional[str]:
        targets = self._extract_at_targets(event)
        if len(targets) > 1:
            raise TargetSelectionError(self.t("target.too_many"))
        return targets[0] if targets else None

    @staticmethod
    def _parse_int_safe(value) -> Optional[int]:
        s = str(value).strip() if value is not None else ""
        return int(s) if s.isascii() and s.isdecimal() else None

    def _resolve_target(self, event: AstrMessageEvent, identifier) -> Optional[str]:
        """优先真实 @，其次网站ID/QQ号/OpenID；不从显示昵称猜测身份。"""
        target = self._extract_at_qq(event)
        if target is not None:
            return target
        raw = str(identifier or "").strip()
        if not raw or raw.startswith("@") or any(c.isspace() for c in raw):
            compat = getattr(self, "_qq_compat", None)
            if compat is not None:
                compat.probe_missing_target(event)
            return None
        return raw

    async def _refresh_balance_cache(self, identifier):
        _, binding = await self.core.lookup_binding(identifier)
        if binding:
            site_id = binding['website_user_id']
            data = await self.core.get_api_user_data(site_id)
            if data:
                self._balance_cache[site_id] = (
                    binding.get('qq_id', binding.get('openid')), data.get('quota', 0)
                )

    def _install_qq_mention_compat(self):
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        if manager is None:
            return
        if getattr(self, "_qq_compat", None) is None:
            self._qq_compat = QQMentionCompat(logger)
        self._qq_compat.install_bare_command_wake()
        for platform in manager.get_insts():
            from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import QQOfficialPlatformAdapter
            if isinstance(platform, QQOfficialPlatformAdapter):
                self._qq_compat.install(platform.get_client())

    async def initialize(self):
        try:
            self._install_qq_mention_compat()
            init_success = await self.core.initialize()
        except BaseException:
            # Installation precedes core startup: failed/cancelled startup must
            # not leave class-level command gates or client callbacks behind.
            compat = getattr(self, "_qq_compat", None)
            if compat is not None:
                await compat.aclose()
                self._qq_compat = None
            raise
        if init_success:
            logger.info("[NewAPI Suite] 核心服务初始化成功。" )
        else:
            logger.error("[NewAPI Suite] 核心服务初始化失败。" )

    @filter.on_platform_loaded()
    async def on_qq_platform_loaded(self):
        self._install_qq_mention_compat()

    @filter.command("提及诊断")
    @guard_errors
    @require_group_whitelist
    async def handle_mention_diagnostic(self, event: AstrMessageEvent, arguments: GreedyStr):
        """只读检查成员提及字段及当前群接收范围，不查询或修改网站账户。"""
        from botpy.message import GroupMessage
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if event.get_platform_name() != "qq_official" or not isinstance(raw, GroupMessage):
            yield self._reply(event, "提及诊断仅支持 QQ 官方群消息。")
            return
        summary = mention_summary(raw)
        observed = getattr(event.message_obj, "_newapi_mention_diagnostic", {})
        event_type = observed.get("event", "unknown")
        if event_type not in ("GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"):
            event_type = "未记录"
        targets = self._extract_at_targets(event)
        # Limit QQ's 30 QPM endpoint across all groups. No group IDs are retained.
        import time
        now = time.monotonic()
        last = getattr(self, "_qq_state_query_at", float("-inf"))
        if now - last < 3:
            mode = "暂未查询（诊断间隔至少 3 秒）"
        else:
            self._qq_state_query_at = now
            mode = await query_group_receive_mode(event)
        lines = [
            "QQ 成员提及诊断（只读）",
            f"事件类型：{event_type}",
            f"原始 mentions 字段：{'存在' if summary['raw_mentions_present'] else '缺失'}",
            f"原始 mentions 数：{summary['raw_mentions_count']}",
            f"SDK mentions 数：{summary['sdk_mentions_count']}",
            f"正文提及标记数：{summary['content_markup_count']}",
            f"可用成员身份数：{summary['raw_member_count']}",
            f"补入成员 At 数：{observed.get('member_at_added', 0)}",
            f"最终目标数：{len(targets)}",
            f"群接收模式：{mode}",
        ]
        if len(targets) == 1:
            lines.append("已识别一个目标；本次未查询或修改网站余额。")
        elif len(targets) > 1:
            lines.append("识别到多个目标；单账号命令会拒绝执行。")
        elif summary['content_markup_count']:
            lines.append("存在正文标记但缺少可确认的成员映射，需要继续检查格式；未猜测目标。")
        elif not summary['raw_available']:
            lines.append("SDK 未保留原始事件，不能据此判断 QQ 是否下发成员身份。")
        else:
            lines.append("此条消息未提供可用目标。若确实选择了成员 @，需要核查 QQ 下发数据。")
        lines.append("接收模式 all 只表示接收范围，不保证成员提及完整。")
        yield self._reply(event, "\n".join(lines))

    async def terminate(self):
        """Remove owned instance hooks and clear the binding cache on unload."""
        compat = getattr(self, "_qq_compat", None)
        if compat is not None:
            await compat.aclose()
            self._qq_compat = None
        await self.delete_kv_data("binding_cache")
        logger.info("[NewAPI Suite] KV 绑定缓存已清空。")


    @filter.command("中转站指令", alias={"中转站帮助", "指令大全"})
    @guard_errors
    async def handle_tx_help(self, event: AstrMessageEvent):
        """推送中转站套件指令大全；免 @兼容对普通群消息同样生效。"""
        text = self.t("help.header", version=PLUGIN_VERSION) + self.t("help.body")
        max_stake = float(config_get(self.config, 'pk_settings.max_stake', 100) or 0)
        if config_get(self.config, 'pk_settings.enabled', True) and max_stake > 0:
            text += "\n" + self.t("pk.max_hint", max=self._fmt_quota(max_stake))
        yield self._reply(event, text)

    @filter.command("pingapi")
    @guard_errors
    async def handle_ping_command(self, event: AstrMessageEvent):
        """响应ping命令，并报告数据库与 New API 连接状态。"""
        db_status = self.t("ping.connected") if self.core.is_db_ready() else self.t("ping.disconnected")
        api_status = self.t("ping.connected") if await self.core.check_api_connection() else self.t("ping.disconnected")
        engine = "SQLite" if self.core.db_mode == "sqlite" else "MySQL"
        reply = (
            f"{self.t('ping.running', version=PLUGIN_VERSION)}\n"
            "--------------------\n"
            f"{self.t('ping.db_engine')}: {engine}\n"
            f"{self.t('ping.db_status')}: {db_status}\n"
            f"{self.t('ping.api_status')}: {api_status}"
        )
        yield self._reply(event, reply)

    @staticmethod
    def _mask_email(email: str) -> str:
        """打码邮箱本地名，避免在群里泄露完整邮箱地址。"""
        text = str(email or "").strip()
        if not text or "@" not in text:
            return text
        local, domain = text.rsplit("@", 1)
        masked = (local[0] + "***") if len(local) > 1 else "***"
        return f"{masked}@{domain}"

    @filter.command("查ID", alias={"查用户", "用户ID"})
    @guard_errors
    async def handle_search_user(self, event: AstrMessageEvent, arguments: GreedyStr):
        """按 NewAPI 用户名或绑定邮箱查询用户 ID（所有用户可用，邮箱打码）。"""
        keyword = str(arguments or "").strip()
        if not keyword:
            yield self._reply(event, self.t("search.usage"))
            return
        users = await self.core.search_api_users(keyword)
        if users is None:
            yield self._reply(event, self.t("search.failed"))
            return
        if not users:
            yield self._reply(event, self.t("search.not_found", keyword=keyword))
            return
        lines = [self.t("search.header", keyword=keyword)]
        for user in users:
            lines.append(self.t(
                "search.line", user_id=user["user_id"],
                username=user.get("username") or "-",
                email=self._mask_email(user.get("email") or ""),
            ))
        yield self._reply(event, "\n".join(lines))

    @filter.command("查询余额")
    @guard_errors
    @require_group_whitelist
    @require_binding
    async def handle_query_balance(self, event: AstrMessageEvent):
        """允许已绑定用户查询网站余额。"""
        binding = event.binding
        website_user_id = binding['website_user_id']
        api_user_data = await self.core.get_api_user_data(website_user_id)

        if not api_user_data:
            yield self._reply(event, self.t("query_balance.failed"))
            return

        binding_conf = config_get(self.config, 'binding_settings', {})
        ratio = binding_conf.get('quota_display_ratio', 500000)
        display_quota = api_user_data.get("quota", 0) / ratio

        reply = self.t("query_balance.success", site_id=website_user_id, quota=f"{display_quota:.6f}")

        # 顺便更新余额缓存，供排行榜使用（OpenID 绑定无 qq_id，回退存 openid 身份）
        self._balance_cache[website_user_id] = (
            binding.get('qq_id', binding.get('openid')),
            api_user_data.get("quota", 0),
        )
        
        yield self._reply(event, reply)

    @filter.command("查余额")
    @guard_errors
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_query_other_balance(self, event: AstrMessageEvent, identifier: str = ""):
        """(管理员) 智能识别 @ 提及、网站ID或QQ号，查询其网站余额。"""
        target_id = self._resolve_target(event, identifier)
        if target_id is None:
            yield self._reply(event, self.t("common.at_or_id_required"))
            return
        id_type, binding = await self.core.lookup_binding(target_id)
        if id_type == "NOT_FOUND":
            yield self._reply(event, self.t("query_other.not_found", id=target_id))
            return

        website_user_id = binding['website_user_id']
        api_user_data = await self.core.get_api_user_data(website_user_id)
        if not api_user_data:
            yield self._reply(event, self.t("query_other.failed"))
            return

        ratio = config_get(self.config, 'binding_settings.quota_display_ratio', 500000)
        display_quota = api_user_data.get("quota", 0) / ratio
        label = self.t({"WEBSITE_ID": "query_other.label_website", "QQ_ID": "query_other.label_qq", "OPENID": "query_other.label_openid"}[id_type])

        reply = self.t("query_other.success", label=label, id=target_id, site_id=website_user_id, quota=f"{display_quota:.6f}")
        yield self._reply(event, reply)

    @filter.command("绑定")
    @guard_errors
    async def handle_bind_command(self, event: AstrMessageEvent, website_user_id: str = ""):
        """处理用户绑定请求，并执行校验。支持 QQ 号绑定 与（开启开关后）OpenID 绑定。"""
        # 网站ID 人工校验：缺失/非数字时给出人类可读提示，避免框架类型转换直接抛异常
        raw_id = str(website_user_id or "").strip()
        if not raw_id:
            yield self._reply(event, self.t("bind.id_required"))
            return
        if not raw_id.isdigit():
            yield self._reply(event, self.t("bind.id_invalid", input=raw_id))
            return
        site_id = int(raw_id)

        binding_conf = config_get(self.config, 'binding_settings', {})
        sender_id = event.get_sender_id()
        openid_enabled = binding_conf.get('enable_openid_binding', False)
        if not self._is_wild_bot_sender(event):
            if not openid_enabled:
                yield self._reply(event, self.t("bind.openid_disabled"))
                return
            openid = str(sender_id).strip()
            yield self._reply(event, await self._perform_openid_binding(event, openid, site_id))
            return

        user_qq_id = int(sender_id)

        error_message = (
            await self._check_self_binding(user_qq_id) or
            await self._check_qq_level(event, user_qq_id) or
            await self._check_user_blacklist(user_qq_id) or
            await self._check_website_id_blacklist(site_id) or
            await self._check_api_user_exists(site_id) or
            await self._check_id_uniqueness(site_id)
        )
        
        if error_message:
            yield self._reply(event, error_message)
            return
        
        yield self._reply(event, self.t("bind.validating"))
        
        success, message = await self._perform_binding_ritual(user_qq_id, site_id)
        
        if success:
            await self._update_binding_cache(site_id, user_qq_id)
            await self._send_success_pm(event, user_qq_id, site_id)
        
        yield self._reply(event, message)

    @filter.command("签到")
    @guard_errors
    @require_group_whitelist
    @require_binding
    async def handle_check_in(self, event: AstrMessageEvent):
        """处理用户每日签到请求（QQ 绑定与 OpenID 绑定均可签到，支持「野机优先」）。"""
        user_qq_id = event.get_sender_id()
        binding = event.binding

        # 「野机优先」：本次请求经官机（OpenID 绑定）到达、且该网站账号同时绑有 QQ 号时，
        # 先让行一小段时间，使同一用户在野机侧的并发签到稳定获胜；仅官机单独触发时只是多等固定延迟。
        if (
            config_get(self.config, 'check_in_settings.wild_bot_priority', True)
            and binding.get('openid')
            and await self.core.get_user_by_website_id(binding['website_user_id'])
        ):
            logger.info(
                "[NewAPI] 野机优先：OpenID 签到让行 %.1fs（网站ID %s）",
                _WILD_PRIORITY_YIELD_SECONDS, binding['website_user_id'],
            )
            await asyncio.sleep(_WILD_PRIORITY_YIELD_SECONDS)

        status, details = await self.core.perform_check_in(user_qq_id, binding=binding)
        
        check_in_conf = config_get(self.config, 'check_in_settings', {})
        
        reply = ""
        match status:
            case "SUCCESS":
                first_bonus_enabled = check_in_conf.get('first_check_in_bonus_enabled', False)
                ratio = config_get(self.config, 'binding_settings.quota_display_ratio', 500000)

                if details["is_first"] and first_bonus_enabled:
                    template = check_in_conf.get('first_check_in_success_template')
                elif details["is_doubled"]:
                    template = check_in_conf.get('check_in_doubled_template')
                else:
                    template = check_in_conf.get('check_in_success_template')
                
                reply = template.format(
                    display_added=f"{details['display_added']:.6f}", 
                    display_total=f"{details['display_total']:.6f}",
                    user_qq=details['user_qq'],
                    site_id=details['site_id']
                )
                # 签到成功后顺便更新余额缓存，供排行榜使用
                new_raw_quota = int(details['display_total'] * ratio)
                self._balance_cache[details['site_id']] = (details['user_qq'], new_raw_quota)
            case "DISABLED":
                reply = self.t("check_in.disabled")
            case "ALREADY_CHECKED_IN":
                reply = self.t("check_in.already")
            case "API_USER_NOT_FOUND":
                reply = self.t("check_in.api_user_not_found")
            case "API_UPDATE_FAILED":
                reply = self.t("check_in.api_update_failed")
            case _:
                reply = self.t("check_in.unknown")
        
        yield self._reply(event, reply)
    @filter.command("解绑")
    @guard_errors
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_unbind_command(self, event: AstrMessageEvent, website_user_id: str = ""):
        """(管理员) 强制解除指定网站ID的绑定。"""
        # 人工校验：非数字时给出人类可读提示，避免框架类型转换直接抛异常
        raw_id = str(website_user_id or "").strip()
        if not raw_id:
            yield self._reply(event, self.t("bind.id_required"))
            return
        if not raw_id.isdigit():
            yield self._reply(event, self.t("bind.id_invalid", input=raw_id))
            return
        site_id = int(raw_id)

        success, binding_info = await self.core.purge_user_binding(site_id)
        
        reply = ""
        if success:
            await self._update_binding_cache(site_id, None)
            # QQ 绑定显示 qq_id；仅 OpenID 绑定时显示 openid
            identity = binding_info.get('qq_id', binding_info.get('openid'))
            reply = self.t("unbind.success", site_id=site_id, qq=identity)
        else:
            if binding_info is None:
                reply = self.t("unbind.not_found", site_id=site_id)
            else:
                reply = self.t("unbind.failed", site_id=site_id)
                
        yield self._reply(event, reply)

    @filter.command("查询")
    @guard_errors
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_universal_lookup(self, event: AstrMessageEvent, identifier: str = ""):
        """(管理员) 智能查询，自动识别网站ID或QQ号。"""
        target_id = self._resolve_target(event, identifier)
        if target_id is None:
            yield self._reply(event, self.t("common.at_or_id_required"))
            return
        id_type, binding = await self.core.lookup_binding(target_id)
        if binding is None:
            yield self._reply(event, self.t("lookup.not_found", id=target_id))
            return

        site_id = binding['website_user_id']
        qq_binding = await self.core.get_user_by_website_id(site_id)
        openid_binding = await self.core.get_openid_by_website_id(site_id)
        label_key = {"WEBSITE_ID": "query_other.label_website", "QQ_ID": "query_other.label_qq",
                     "OPENID": "query_other.label_openid"}[id_type]
        lines = [self.t("lookup.header", label=self.t(label_key), site_id=site_id)]
        for record, field, label in ((qq_binding, 'qq_id', 'QQ'), (openid_binding, 'openid', 'OpenID')):
            if record:
                when = record.get('binding_time')
                when = when.strftime('%Y-%m-%d %H:%M:%S') if isinstance(when, datetime) else str(when or '-')
                lines.append(self.t("lookup.identity", label=label, identity=record[field], time=when))
        yield self._reply(event, "\n".join(lines))

    @filter.command("new-tx")
    @guard_errors
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_db_transfer(self, event: AstrMessageEvent, action: str = ""):
        """(管理员) 数据库导入导出：导出=当前库内容写入迁移文件；导入=迁移文件覆盖当前库。"""
        act = str(action or "").strip()
        if act not in ("导出", "导入"):
            yield self._reply(event, self.t("db_transfer.usage"))
            return

        act_label = self.t("db_transfer.act_export") if act == "导出" else self.t("db_transfer.act_import")
        yield self._reply(event, self.t("db_transfer.working", action=act_label))

        try:
            if act == "导出":
                result = await self.core.export_database()
                done_key = "db_transfer.export_done"
            else:
                result = await self.core.import_database()
                done_key = "db_transfer.import_done"
            detail = self._format_transfer_counts(result["counts"])
            total = sum(result["counts"].values())
            reply = self.t(done_key, detail=detail, total=total)
        except FileNotFoundError:
            reply = self.t("db_transfer.no_file")
        except ValueError:
            # 迁移文件为空，安全防护拒绝导入
            reply = self.t("db_transfer.empty_file")
        except Exception as e:
            logger.error(f"数据库{act}操作失败: {e}", exc_info=True)
            reply = self.t("db_transfer.failed", action=act_label, err=e)

        yield self._reply(event, reply)

    def _format_transfer_counts(self, counts: dict) -> str:
        """把各表行数格式化为多行人类可读明细。"""
        labels = {
            "newapi_bindings": "db_transfer.label_bindings",
            "newapi_openid_bindings": "db_transfer.label_openid_bindings",
            "newapi_check_in_state": "db_transfer.label_check_in_state",
            "daily_heist_log": "db_transfer.label_heist_log",
            "newapi_pk_matches": "db_transfer.label_pk",
        }
        return "\n".join(
            self.t("db_transfer.detail_line", label=self.t(key), count=counts.get(table, 0))
            for table, key in labels.items()
        )

    # --- 红包（拼手气） ---

    @filter.command("发红包")
    @guard_errors
    @filter.permission_type(filter.PermissionType.ADMIN)
    @require_group_whitelist
    async def handle_send_red_packet(self, event: AstrMessageEvent, count: str = "", amount: str = ""):
        """(管理员) 发拼手气红包：发红包 数量 总额度（凭空发放，24h 有效）。可配置仅限官机触发。"""
        # 「红包仅官机」：野机的发红包请求同样静默忽略——不回复、零消息量，降低野机风控风险
        if self._red_packet_official_only_blocked(event, "发红包"):
            return

        conf = config_get(self.config, 'red_packet_settings', {})
        if not conf.get('enabled', True):
            yield self._reply(event, self.t("rp.disabled"))
            return

        raw_count = str(count or "").strip()
        raw_amount = str(amount or "").strip()

        if not raw_count:
            yield self._reply(event, self.t("rp.count_required"))
            return
        if not raw_count.isdigit():
            yield self._reply(event, self.t("rp.count_invalid", input=raw_count))
            return
        grab_count = int(raw_count)
        max_count = int(conf.get('max_grab_count', 100))
        if grab_count <= 0 or grab_count > max_count:
            yield self._reply(event, self.t("rp.count_too_large", max=max_count))
            return

        if not raw_amount:
            yield self._reply(event, self.t("rp.amount_required"))
            return
        try:
            total_display = round(float(raw_amount), 6)
        except ValueError:
            yield self._reply(event, self.t("rp.amount_invalid", input=raw_amount))
            return
        if total_display <= 0:
            yield self._reply(event, self.t("rp.amount_invalid", input=raw_amount))
            return
        max_total = float(conf.get('max_total_display', 100000))
        if total_display > max_total:
            yield self._reply(event, self.t("rp.amount_too_large", max=f"{max_total:g}"))
            return

        # 每份至少 1 原始额度，总额过低无法拆分
        ratio = config_get(self.config, 'binding_settings.quota_display_ratio', 500000) or 1
        if int(round(total_display * ratio)) < grab_count:
            yield self._reply(event, self.t("rp.too_small", count=grab_count))
            return

        creator = str(event.get_sender_id())
        result = await self.core.create_red_packet(creator, total_display, grab_count)
        if not result or result.get('error') or not result.get('pid'):
            yield self._reply(event, self.t("rp.api_error"))
            return
        amount_str = f"{total_display:.6f}".rstrip('0').rstrip('.')
        created_msg = self.t(
            "rp.created", pid=result.get('code', result['pid']), count=grab_count,
            amount=amount_str, hours=result['expire_hours'], creator=creator,
        )
        # 仅官机模式下，公告附提示，引导用户去官方机器人处抢
        if self._red_packet_official_only():
            created_msg += self.t("rp.official_only_hint")
        yield self._reply(event, created_msg)

    @filter.command("抢红包")
    @guard_errors
    @require_group_whitelist
    async def handle_grab_red_packet(self, event: AstrMessageEvent, packet_id: str = ""):
        """抢拼手气红包：抢红包 红包代码（字母数字，发红包时公布）。额度直接入账网站余额。可配置仅限官机。"""
        # 「红包仅官机」：野机（数字 QQ 身份）的请求静默忽略——不回复、零消息量，降低野机风控风险
        if self._red_packet_official_only_blocked(event, "抢红包"):
            return

        raw_ref = str(packet_id or "").strip()
        if not raw_ref:
            yield self._reply(event, self.t("rp.pid_required"))
            return
        if not re.fullmatch(r"[a-zA-Z0-9]{1,16}", raw_ref):
            yield self._reply(event, self.t("rp.pid_invalid", input=raw_ref))
            return

        identity = str(event.get_sender_id())
        binding = await self.core.get_user_by_identity(self._sender_identity(event))
        if not binding:
            yield self._reply(event, self.t("not_bound"))
            return
        site_id = binding['website_user_id']
        grabber_name = self._sender_display(event)

        status, details = await self.core.grab_red_packet(
            raw_ref.lower(), identity, site_id, grabber_name=grabber_name
        )

        reply = ""
        match status:
            case "SUCCESS":
                amount_str = f"{details['amount_display']:.6f}".rstrip('0').rstrip('.')
                reply = self.t("rp.success", grabber=grabber_name,
                               amount=amount_str,
                               remain=details['remain'], total=details['total'])
                # 抢完排行榜：金额由多到少公布领取者（超过20人只展示前20）
                if details.get("exhausted") and details.get("entries"):
                    reply += "\n\n" + self.t("rp.rank.header") + "\n" + self._format_rp_rank(details["entries"])
                # 抢到后顺便更新余额缓存，供排行榜使用
                data = await self.core.get_api_user_data(site_id)
                if data:
                    self._balance_cache[site_id] = (
                        binding.get('qq_id', binding.get('openid')), data.get('quota', 0)
                    )
            case "ALREADY":
                reply = self.t("rp.already")
            case "EMPTY":
                reply = self.t("rp.empty")
            case "EXPIRED":
                reply = self.t("rp.expired")
            case "NOT_FOUND":
                reply = self.t("rp.not_found", pid=raw_ref)
            case "DISABLED":
                reply = self.t("rp.disabled")
            case _:
                reply = self.t("rp.api_error")

        yield self._reply(event, reply)

    def _format_rp_rank(self, entries: list) -> str:
        """红包抢完排行文本：金额降序，前三名带奖牌，最多展示 20 人并注明剩余人数。"""
        medals = ["🥇 ", "🥈 ", "🥉 "]
        lines = []
        for i, e in enumerate(entries[:20]):
            rank = medals[i] if i < 3 else f"{i + 1}. "
            amt = f"{e['display']:.6f}".rstrip('0').rstrip('.')
            lines.append(self.t("rp.rank.line", rank=rank, name=e['name'], amount=amt))
        text = "\n".join(lines)
        if len(entries) > 20:
            text += "\n" + self.t("rp.rank.more", n=len(entries) - 20)
        return text

    # --- 个人红包（普通用户，扣自己额度）辅助方法 ---

    def _user_rp_date_key(self) -> str:
        """个人红包每日计数的日期键（与签到一致使用配置时区）。"""
        offset = float(config_get(self.config, 'check_in_settings.timezone_offset_hours', 0) or 0)
        return (datetime.utcnow() + timedelta(hours=offset)).date().isoformat()

    async def _load_user_rp_daily(self) -> Dict[str, Any]:
        """加载当日个人红包计数桶；跨天自动重置。"""
        key = self._user_rp_date_key()
        store = await self.get_kv_data("rp_user_daily", {}) or {}
        if store.get("date") != key:
            store = {"date": key, "counts": {}}
        store.setdefault("counts", {})
        return store

    async def _user_rp_used_today(self, site_id) -> int:
        """该网站账号今日已发个人红包次数。"""
        store = await self._load_user_rp_daily()
        return int(store["counts"].get(str(site_id), 0))

    async def _bump_user_rp_count(self, site_id):
        """发送成功后计数 +1（锁内重读防覆盖）。"""
        async with self._kv_lock:
            store = await self._load_user_rp_daily()
            counts = store["counts"]
            counts[str(site_id)] = int(counts.get(str(site_id), 0)) + 1
            await self.put_kv_data("rp_user_daily", store)

    async def _rp_verified_sites(self) -> set:
        """已完成访问令牌验证的网站 ID 集合（KV：site_id → ISO 时间）。"""
        data = await self.get_kv_data("rp_user_verified", {}) or {}
        return set(data.keys())

    async def _mark_rp_verified(self, site_id):
        """标记网站账号已通过访问令牌验证（一次验证永久生效）。"""
        async with self._kv_lock:
            data = await self.get_kv_data("rp_user_verified", {}) or {}
            data[str(site_id)] = datetime.utcnow().isoformat()
            await self.put_kv_data("rp_user_verified", data)

    @filter.command("验证令牌")
    @guard_errors
    @require_binding
    async def handle_verify_token(self, event: AstrMessageEvent, token: str = ""):
        """个人红包身份验证：验证令牌 [网站访问令牌]，一次通过后永久生效。"""
        raw = str(token or "").strip().strip('"').strip("'")
        binding = event.binding
        site_id = int(binding['website_user_id'])

        if not raw:
            yield self._reply(event, self.t("rp.verify.token_required"))
            return

        verified = await self.core.get_self_by_user_token(raw, expected_user_id=site_id)
        if not verified or verified.get("user_id") != site_id:
            logger.warning(f"[个人红包] 令牌验证失败：site={site_id}")
            yield self._reply(event, self.t("rp.verify.failed"))
            return

        await self._mark_rp_verified(site_id)
        logger.info(f"[个人红包] 网站ID {site_id} 访问令牌验证通过")
        yield self._reply(event, self.t("rp.verify.success", site_id=site_id))

    @filter.command("个人红包")
    @guard_errors
    @require_group_whitelist
    @require_binding
    async def handle_user_red_packet(self, event: AstrMessageEvent, count: str = "", amount: str = ""):
        """普通用户发拼手气红包：个人红包 [份数] [总额度]。

        规则：从自己余额扣款；每满 user_send_balance_per_send 余额可发 1 次/日，
        每日上限 user_send_max_per_day 次；首次发前需「验证令牌」完成身份验证。
        """
        # 「红包仅官机」：野机的个人红包请求同样静默忽略——不回复、零消息量
        if self._red_packet_official_only_blocked(event, "个人红包"):
            return

        conf = config_get(self.config, 'red_packet_settings', {})
        if not conf.get('enabled', True):
            yield self._reply(event, self.t("rp.disabled"))
            return
        if not conf.get('user_send_enabled', True):
            yield self._reply(event, self.t("rp.user.disabled"))
            return

        # 参数校验（人类可读提示；帮助文案指向「个人红包」而非管理员的「发红包」）
        raw_count = str(count or "").strip()
        raw_amount = str(amount or "").strip()
        if not raw_count:
            yield self._reply(event, self.t("rp.user.count_required"))
            return
        if not raw_count.isdigit():
            yield self._reply(event, self.t("rp.user.count_invalid", input=raw_count))
            return
        grab_count = int(raw_count)
        max_count = int(conf.get('max_grab_count', 100))
        if grab_count <= 0 or grab_count > max_count:
            yield self._reply(event, self.t("rp.user.count_too_large", max=max_count))
            return
        if not raw_amount:
            yield self._reply(event, self.t("rp.user.amount_required"))
            return
        try:
            total_display = round(float(raw_amount), 6)
        except ValueError:
            yield self._reply(event, self.t("rp.user.amount_invalid", input=raw_amount))
            return
        if total_display <= 0:
            yield self._reply(event, self.t("rp.user.amount_invalid", input=raw_amount))
            return

        identity = str(event.get_sender_id())
        binding = event.binding
        site_id = int(binding['website_user_id'])
        ratio = config_get(self.config, 'binding_settings.quota_display_ratio', 500000) or 1

        # 首次发红包前的身份验证门槛
        if str(site_id) not in await self._rp_verified_sites():
            yield self._reply(event, self.t("rp.user.not_verified"))
            return

        # 拉取实时余额 → 计算今日可发次数
        api_user = await self.core.get_api_user_data(site_id)
        if not api_user:
            yield self._reply(event, self.t("rp.user.balance_unavailable"))
            return
        balance_raw = int(api_user.get("quota", 0) or 0)
        balance_display = balance_raw / ratio
        per_send = float(conf.get('user_send_balance_per_send', 5) or 5)
        max_day = int(conf.get('user_send_max_per_day', 10))
        allowed_today = min(max_day, int(balance_display // per_send))
        used_today = await self._user_rp_used_today(site_id)
        remaining = max(0, allowed_today - used_today)
        if allowed_today <= 0 or remaining <= 0:
            yield self._reply(event, self.t(
                "rp.user.limit_reached",
                balance=f"{balance_display:.6f}".rstrip('0').rstrip('.'),
                per=f"{per_send:g}", max=max_day, used=used_today,
            ))
            return

        total_raw = int(round(total_display * ratio))
        if total_display > balance_display:
            yield self._reply(event, self.t(
                "rp.user.exceeds_balance",
                balance=f"{balance_display:.6f}".rstrip('0').rstrip('.'),
            ))
            return
        # 每份至少 1 原始额度，总额过低无法拆分
        if total_raw < grab_count:
            yield self._reply(event, self.t("rp.too_small", count=grab_count))
            return

        # 锁内「查余额→扣款→建包→计数」，防并发双花；建包失败自动退款
        async with self.core._get_user_rp_lock(site_id):
            fresh = await self.core.get_api_user_data(site_id)
            fresh_balance_raw = int(fresh.get("quota", 0) or 0) if fresh else 0
            if total_raw > fresh_balance_raw:
                yield self._reply(event, self.t(
                    "rp.user.exceeds_balance",
                    balance=f"{fresh_balance_raw / ratio:.6f}".rstrip('0').rstrip('.'),
                ))
                return
            if not await self.core.manage_user_quota(site_id, "subtract", total_raw):
                yield self._reply(event, self.t("rp.user.deduct_failed"))
                return

            result = await self.core.create_red_packet(identity, total_display, grab_count)
            if not result or result.get('error') or not result.get('pid'):
                refunded = await self.core.manage_user_quota(site_id, "add", total_raw)
                if not refunded:
                    logger.error(f"[个人红包] 建包失败且退款失败！site={site_id} 金额={total_display} 请人工处理")
                yield self._reply(event, self.t(
                    "rp.user.create_failed",
                    amount=f"{total_display:.6f}".rstrip('0').rstrip('.'),
                ))
                return

            await self._bump_user_rp_count(site_id)

        new_balance_display = (fresh_balance_raw - total_raw) / ratio
        amount_str = f"{total_display:.6f}".rstrip('0').rstrip('.')
        created_msg = self.t(
            "rp.user.created", creator=f"网站ID {site_id}", pid=result.get('code', result['pid']), count=grab_count,
            amount=amount_str, hours=result['expire_hours'],
            balance=f"{new_balance_display:.6f}".rstrip('0').rstrip('.'),
            left=max(0, remaining - 1),
        )
        # 「红包仅官机」开启时，公告同样提示只能经官方机器人抢
        if self._red_packet_official_only():
            created_msg += self.t("rp.official_only_hint")
        # 更新余额缓存供排行榜使用
        self._balance_cache[site_id] = (
            binding.get('qq_id', binding.get('openid')), fresh_balance_raw - total_raw
        )
        yield self._reply(event, created_msg)

    @filter.command("调整余额")
    @guard_errors
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_adjust_balance(self, event: AstrMessageEvent, arguments: GreedyStr):
        """(管理员) 调整余额 目标 金额；完整接收文本，兼容 @昵称中包含空格。"""
        at_target = self._extract_at_qq(event)
        raw = str(arguments or "").strip()
        parts = raw.split()
        if at_target:
            target_id = at_target
            amount_text = parts[-1] if parts else ""
        elif len(parts) == 2:
            target_id = self._resolve_target(event, parts[0])
            amount_text = parts[1]
        else:
            yield self._reply(event, self.t("adjust.usage"))
            return
        if target_id is None:
            yield self._reply(event, self.t("common.at_or_id_required"))
            return
        try:
            amount = float(amount_text)
        except (TypeError, ValueError):
            yield self._reply(event, self.t("adjust.invalid_amount"))
            return
        if not math.isfinite(amount) or amount == 0:
            yield self._reply(event, self.t("adjust.invalid_amount"))
            return

        status, details = await self.core.adjust_balance_by_identifier(target_id, amount)
        reply = ""
        match status:
            case "SUCCESS":
                key = "adjust.success_inc" if amount >= 0 else "adjust.success_dec"
                reply = self.t(key, site_id=details['website_user_id'], amount=f"{abs(amount):.6f}", total=f"{details['new_display_quota']:.6f}")
                await self._refresh_balance_cache(f"site:{details['website_user_id']}")
            case "APPLIED_BALANCE_UNAVAILABLE":
                self._balance_cache.pop(details['website_user_id'], None)
                reply = self.t("adjust.applied_unavailable", site_id=details['website_user_id'])
            case "USER_NOT_FOUND":
                reply = self.t("adjust.not_found", id=target_id)
            case "API_FETCH_FAILED":
                reply = self.t("adjust.fetch_failed", site_id=details['website_user_id'])
            case "API_UPDATE_FAILED":
                reply = self.t("adjust.update_failed", site_id=details['website_user_id'])
            case _:
                reply = self.t("adjust.invalid_amount")
        yield self._reply(event, reply)

    @filter.command("打劫")
    @guard_errors
    @require_group_whitelist
    async def handle_heist_command(self, event: AstrMessageEvent, identifier: str = ""):
        """(娱乐) 打劫目标：@ 提及，或输入 QQ 号 / OpenID（开启 openid 绑定后支持）。"""
        robber_qq_id = self._sender_identity(event)

        targets = self._extract_at_targets(event)
        if len(targets) > 1:
            yield self._reply(event, self.t("heist.too_many"))
            return
        victim_identifier = self._resolve_target(event, identifier)
        if victim_identifier is None:
            yield self._reply(event, self.t("heist.no_target"))
            return

        status, details = await self.heist_handler.execute_heist(robber_qq_id, victim_identifier)
        
        # 4. 根据结果生成回复
        heist_conf = config_get(self.config, 'heist_settings', {})
        reply = ""

        # --- 缓存模板 ---
        success_template = heist_conf.get('success_template', "成功: +{gain:.2f}")
        critical_template = heist_conf.get('critical_template', "暴击: +{gain:.2f}")
        failure_template = heist_conf.get('failure_template', "失败: -{penalty:.2f}")
        disabled_template = heist_conf.get('disabled_template', "⚔️ 打劫活动尚未开启。" )
        robber_not_bound_template = heist_conf.get('robber_not_bound_template', "🤔 请先绑定账号。" )
        victim_not_found_template = heist_conf.get('victim_not_found_template', "💨 未找到目标 {victim_identifier}。" )
        cannot_rob_self_template = heist_conf.get('cannot_rob_self_template', "🤦‍♂️ 不能打劫自己。" )
        attempts_exceeded_template = heist_conf.get('attempts_exceeded_template', "🥵 次数用尽。" )
        defenses_exceeded_template = heist_conf.get('defenses_exceeded_template', "🛡️ 对方已有防备 (ID:{victim_id})。" )
        cooldown_template = heist_conf.get('cooldown_template', "⏳ 冷却中，剩余 {remaining_time} 秒。")
        # --- 缓存结束 ---

        match status:
            case "SUCCESS":
                reply = success_template.format(gain=details['gain'])
                # 打劫成功后顺便更新余额缓存（抢劫者+受害者），供排行榜使用
                await self._refresh_balance_cache(robber_qq_id)
                await self._refresh_balance_cache(victim_identifier)
            case "CRITICAL":
                reply = critical_template.format(gain=details['gain'])
                # 同上
                await self._refresh_balance_cache(robber_qq_id)
                await self._refresh_balance_cache(victim_identifier)
            case "FAILURE":
                reply = failure_template.format(penalty=details['penalty'])
                await self._refresh_balance_cache(robber_qq_id)
                await self._refresh_balance_cache(victim_identifier)
            case "DISABLED":
                reply = disabled_template
            case "ROBBER_NOT_BOUND":
                reply = robber_not_bound_template
            case "VICTIM_NOT_FOUND":
                reply = victim_not_found_template.format(victim_identifier=f" {victim_identifier}")
            case "CANNOT_ROB_SELF":
                reply = cannot_rob_self_template
            case "ATTEMPTS_EXCEEDED":
                reply = attempts_exceeded_template
            case "DEFENSES_EXCEEDED":
                reply = defenses_exceeded_template.format(victim_id=details['victim_id'])
            case "COOLDOWN_ACTIVE":
                reply = cooldown_template.format(remaining_time=details['remaining_time'])
            case "API_ERROR":
                reply = self.t("heist.api_error")
            case _:
                reply = self.t("heist.unknown")
        
        yield self._reply(event, reply)

    @filter.command("PK", alias={"pk"})
    @guard_errors
    @require_group_whitelist
    @require_binding
    async def handle_pk_command(self, event: AstrMessageEvent, arguments: GreedyStr):
        """(娱乐) 发起 PK：PK 对方网站ID 金额。发起方立即扣款，对方 5 分钟内应战，超时自动退还。"""
        at_target = self._extract_at_qq(event)
        raw = str(arguments or "").strip()
        parts = raw.split()
        if at_target:
            target_identifier = at_target
            amount_text = parts[-1] if parts else ""
        elif len(parts) >= 2:
            target_identifier = self._resolve_target(event, parts[0])
            amount_text = parts[1]
        else:
            max_stake = float(config_get(self.config, 'pk_settings.max_stake', 100) or 0)
            usage = self.t("pk.usage")
            if max_stake > 0:
                usage += "\n" + self.t("pk.max_hint", max=self._fmt_quota(max_stake))
            yield self._reply(event, usage)
            return
        if target_identifier is None:
            yield self._reply(event, self.t("common.at_or_id_required"))
            return
        try:
            amount = float(amount_text)
        except (TypeError, ValueError):
            yield self._reply(event, self.t("pk.amount_invalid"))
            return

        status, details = await self.pk_handler.create_challenge(
            self._sender_identity(event), target_identifier, amount)
        expiry_seconds = int(config_get(self.config, 'pk_settings.expiry_seconds', 300) or 300)
        match status:
            case "DISABLED":
                reply = self.t("pk.disabled")
            case "INVALID_AMOUNT":
                reply = self.t("pk.amount_invalid")
            case "STAKE_TOO_LARGE":
                reply = self.t("pk.stake_too_large", max=self._fmt_quota(details['max']))
            case "CHALLENGER_NOT_BOUND":
                reply = self.t("not_bound")
            case "TARGET_NOT_FOUND":
                reply = self.t("pk.target_not_found", id=details['id'])
            case "CANNOT_PK_SELF":
                reply = self.t("pk.self")
            case "ALREADY_PENDING":
                reply = self.t("pk.already_pending")
            case "INSUFFICIENT_BALANCE":
                reply = self.t("pk.insufficient_balance",
                               need=self._fmt_quota(details['need']),
                               balance=self._fmt_quota(details['balance']))
            case "DEDUCT_FAILED":
                reply = self.t("pk.deduct_failed")
            case "DB_FAILED":
                reply = self.t("pk.db_failed")
            case "CREATED":
                reply = self.t("pk.created",
                               challenger=details['challenger_site'],
                               opponent=details['opponent_site'],
                               amount=self._fmt_quota(details['stake_display']),
                               minutes=max(1, expiry_seconds // 60))
                await self._refresh_balance_cache(f"site:{details['challenger_site']}")
            case "AUTO_SETTLED":
                reply = self._pk_settled_reply(details)
                await self._refresh_balance_cache(f"site:{details['winner_site']}")
                await self._refresh_balance_cache(f"site:{details['loser_site']}")
            case "AUTO_ACCEPT_FAILED_REFUNDED":
                reply = self.t("pk.auto_failed")
                await self._refresh_balance_cache(f"site:{details['challenger_site']}")
            case "SETTLE_FAILED_REFUNDED":
                reply = self.t("pk.settle_failed")
            case _:
                reply = self.t("common.unexpected_error", err=status)
        ats = []
        if status == "CREATED":
            ats = await self._pk_at_identities(event, [details['opponent_site']])
        elif status == "AUTO_SETTLED":
            ats = await self._pk_at_identities(
                event, [details['challenger_site'], details['opponent_site']])
        yield self._reply_with_ats(event, ats, reply)

    @filter.command("接受PK", alias={"接受pk", "接PK", "接pk"})
    @guard_errors
    @require_group_whitelist
    @require_binding
    async def handle_accept_pk(self, event: AstrMessageEvent, arguments: GreedyStr = ""):
        """(娱乐) 应战 PK：接受PK [发起人网站ID]；不填则自动应战唯一一局。"""
        at_target = self._extract_at_qq(event)
        raw = str(arguments or "").strip()
        if at_target:
            identifier = at_target
        else:
            parts = raw.split()
            identifier = parts[0] if parts else ""
        status, details = await self.pk_handler.accept_challenge(
            self._sender_identity(event), identifier or None)
        match status:
            case "DISABLED":
                reply = self.t("pk.disabled")
            case "OPPONENT_NOT_BOUND":
                reply = self.t("not_bound")
            case "NOT_FOUND":
                reply = self.t("pk.accept.not_found")
            case "EXPIRED":
                reply = self.t("pk.accept.expired")
            case "MULTIPLE_PENDING":
                reply = self.t("pk.accept.multiple", count=details['count'])
            case "ACCEPT_INSUFFICIENT_BALANCE":
                reply = self.t("pk.accept.insufficient",
                               need=self._fmt_quota(details['need']),
                               balance=self._fmt_quota(details['balance']))
            case "ACCEPT_DEDUCT_FAILED":
                reply = self.t("pk.accept.deduct_failed")
            case "SETTLED":
                reply = self._pk_settled_reply(details)
                await self._refresh_balance_cache(f"site:{details['winner_site']}")
                await self._refresh_balance_cache(f"site:{details['loser_site']}")
            case "SETTLE_FAILED_REFUNDED":
                reply = self.t("pk.settle_failed")
            case _:
                reply = self.t("common.unexpected_error", err=status)
        ats = []
        if status == "SETTLED":
            ats = await self._pk_at_identities(
                event, [details['challenger_site'], details['opponent_site']])
        yield self._reply_with_ats(event, ats, reply)

    @filter.command("榜单")
    @guard_errors
    async def handle_leaderboard(self, event: AstrMessageEvent):
        """展示群内余额榜与打劫榜（余额从用户操作缓存读取，无需查API）。"""
        lb_conf = config_get(self.config, 'leaderboard_settings', {})
        if not lb_conf.get('enabled', False):
            yield self._reply(event, self.t("leaderboard.disabled"))
            return
        top_n = max(1, int(lb_conf.get('top_n', 10)))
        ratio = config_get(self.config, 'binding_settings.quota_display_ratio', 500000)

        # 余额榜：直接从缓存读取并排序（用户每次签到/查余额/打劫时更新缓存）
        balance_lines = self._build_balance_board_from_cache(top_n, ratio)
        # 打劫榜：纯 SQL 聚合
        heist_lines = await self._build_heist_board(top_n, ratio)

        reply = self.t("leaderboard.header", top_n=top_n, balance=balance_lines, heist=heist_lines)
        yield self._reply(event, reply)

    @filter.command("消耗榜")
    @guard_errors
    async def handle_consumption_leaderboard(self, event: AstrMessageEvent):
        """展示全站用户近 N 小时 token 消耗排行榜（用户名 + 已绑定则附 QQ 号），所有用户可用。"""
        conf = config_get(self.config, 'consumption_leaderboard_settings', {})
        if not conf.get('enabled', False):
            yield self._reply(event, self.t("consumption.disabled"))
            return
        top_n = max(1, int(conf.get('top_n', 10)))
        hours = max(1, int(conf.get('window_hours', 24)))

        yield self._reply(event, self.t("consumption.fetching", hours=hours))

        stats = await self.core.get_user_token_consumption(hours=hours)
        if stats is None:
            yield self._reply(event, self.t("consumption.fetch_failed"))
            return
        if not stats:
            yield self._reply(event, self.t("consumption.no_data", hours=hours))
            return

        stats.sort(key=lambda x: x['tokens'], reverse=True)
        top = stats[:top_n]

        ratio = config_get(self.config, 'binding_settings.quota_display_ratio', 500000)
        ratio = ratio if ratio else 1
        show_quota = bool(conf.get('show_quota', False))
        show_qq = bool(conf.get('show_qq', True))

        # 缓存所有有消耗用户的绑定关系到 KV（单个 dict 键），避免每次查 DB
        # 加锁保护读-改-写，避免与绑定/解绑的缓存更新互相覆盖
        cache: dict = {}
        db_lookups = 0
        db_hits = 0
        if show_qq:
            try:
                async with self._kv_lock:
                    cache = await self.get_kv_data("binding_cache", {}) or {}
                    cache_updated = False
                    for s in stats:
                        user_id = str(s["user_id"])
                        if user_id not in cache:
                            db_lookups += 1
                            binding = await self.core.get_user_by_website_id(s["user_id"])
                            if binding:
                                db_hits += 1
                                cache[user_id] = binding['qq_id']
                                cache_updated = True
                    if cache_updated:
                        await self.put_kv_data("binding_cache", cache)
            except Exception as e:
                # 预填充失败不阻断出榜：下方展示层会对上榜用户逐条回退查库
                logger.warning(f"[消耗榜] 绑定缓存预填充失败（将逐条回退查询）: {e}")
            if self._is_debug():
                sample = ",".join(f"{s['user_id']}→{type(s['user_id']).__name__}" for s in top[:3])
                logger.info(
                    f"[消耗榜][DEBUG] show_qq={show_qq} 缓存大小={len(cache)} 窗口用户数={len(stats)} "
                    f"预填充查库={db_lookups}次 命中={db_hits}次 TOP3类型[{sample}]"
                )

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, s in enumerate(top):
            rank = idx + 1
            prefix = medals[idx] if idx < 3 else f"{rank}."
            username = s.get("username") or str(s["user_id"])
            raw_cached = cache.get(str(s["user_id"])) if show_qq else None
            if self._is_debug():
                logger.info(f"[消耗榜][DEBUG] 行{rank} user={s['user_id']!r} 缓存原始值={raw_cached!r}")
            qq_id = raw_cached
            if show_qq and qq_id is None:
                # 展示层兜底：上榜用户缓存未命中时逐条回退查库，
                # 避免缓存被清空（如插件重载）或预填充失败导致整榜无 QQ 号
                try:
                    binding = await self.core.get_user_by_website_id(s["user_id"])
                    if binding:
                        qq_id = binding['qq_id']
                        if self._is_debug():
                            logger.info(f"[消耗榜][DEBUG] 兜底命中 user_id={s['user_id']} → QQ:{qq_id}")
                        try:
                            async with self._kv_lock:
                                fresh = await self.get_kv_data("binding_cache", {}) or {}
                                fresh[str(s["user_id"])] = qq_id
                                await self.put_kv_data("binding_cache", fresh)
                        except Exception as e:
                            logger.warning(f"[消耗榜] 回写绑定缓存失败（不影响本次展示）: {e}")
                    else:
                        if self._is_debug():
                            logger.info(f"[消耗榜][DEBUG] 兜底未命中 user_id={s['user_id']}（数据库无此绑定行，类型={type(s['user_id']).__name__}）")
                except Exception as e:
                    logger.warning(f"[消耗榜] 榜单用户 {s['user_id']} 绑定查询失败: {e}")
            show_bound = show_qq and qq_id is not None
            if self._is_debug():
                logger.info(f"[消耗榜][DEBUG] 行{rank} 最终 qq_id={qq_id!r} show_bound={show_bound}")
            qq = str(qq_id) if show_bound else ""
            if show_quota:
                display_quota = (s.get("quota", 0) or 0) / ratio
                if show_bound:
                    line = self.t("consumption.line_bound_quota", prefix=prefix, username=username,
                                  qq=qq, tokens=f"{s['tokens']:,}",
                                  quota=f"{display_quota:.6f}")
                else:
                    line = self.t("consumption.line_quota", prefix=prefix, username=username,
                                  tokens=f"{s['tokens']:,}", quota=f"{display_quota:.6f}")
            else:
                if show_bound:
                    line = self.t("consumption.line_bound", prefix=prefix, username=username,
                                  qq=qq, tokens=f"{s['tokens']:,}")
                else:
                    line = self.t("consumption.line", prefix=prefix, username=username,
                                  tokens=f"{s['tokens']:,}")
            lines.append(line)

        reply = self.t("consumption.header", top_n=top_n, hours=hours, lines="\n".join(lines))
        # 指纹日志：确认可见回复由本实例/本段代码渲染（排查重复加载的旧实例分流）
        if self._is_debug():
            logger.info(
                f"[消耗榜][DEBUG] 实例#{id(self) % 0xffff} 渲染首行={lines[0] if lines else '(空)'}"
            )
            logger.info(f"[消耗榜][DEBUG] 实例#{id(self) % 0xffff} 渲染全文>>>\n{reply}\n<<<全文结束")
        yield self._reply(event, reply)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_group_decrease(self, event: AstrMessageEvent):
        """监听群成员减少事件，执行解绑并发送通知。"""
        if not isinstance(event, AiocqhttpMessageEvent):
            return

        raw = event.message_obj.raw_message
        if not (
            isinstance(raw, dict)
            and raw.get("post_type") == "notice"
            and raw.get("notice_type") == "group_decrease"
        ):
            return
        
        group_id = raw.get("group_id")
        user_id = raw.get("user_id")

        leave_conf = config_get(self.config, 'group_leave_settings', {})
        monitored_groups_str = leave_conf.get('group_monitoring_list', [])
        monitored_groups = [int(g) for g in monitored_groups_str if str(g).isdigit()]

        if group_id not in monitored_groups:
            return

        binding = await self.core.get_user_by_qq(user_id)
        if not binding:
            logger.info(f"用户 {user_id} 退出了受监控的群 {group_id}，但其未被绑定，无需净化。" )
            return

        website_user_id = binding['website_user_id']
        success, _ = await self.core.purge_user_binding(website_user_id)

        if success:
            await self._update_binding_cache(website_user_id, None)
            logger.info(f"用户 {user_id} (网站ID: {website_user_id}) 的退群净化仪式成功完成。" )
            
            try:
                sub_type = raw.get("sub_type")
                operator_id = raw.get("operator_id")
                bot = event.bot

                user_info = await bot.get_stranger_info(user_id=user_id, no_cache=True)
                user_nickname = user_info.get("nickname", str(user_id))

                announcement = ""
                if sub_type == "leave":
                    announcement = self.t("leave.announcement", nickname=user_nickname, qq=user_id)
                elif sub_type == "kick":
                    operator_info = await bot.get_group_member_info(group_id=group_id, user_id=operator_id, no_cache=True)
                    operator_nickname = operator_info.get("card") or operator_info.get("nickname", str(operator_id))
                    announcement = self.t("kick.announcement", nickname=user_nickname, qq=user_id, op=operator_nickname)
                
                if announcement:
                    await bot.send_group_msg(group_id=group_id, message=announcement)

            except Exception as e:
                logger.error(f"在为用户 {user_id} 发送退群净化通告时发生错误: {e}", exc_info=True)
        
        event.stop_event()

    # --- 排行榜辅助方法 ---

    def _build_balance_board_from_cache(self, top_n: int, ratio: int) -> str:
        """构建余额排行榜：直接读取内存缓存，无需查 API。

        缓存由用户每次操作（签到/查余额/打劫）时顺手更新。
        """
        if not self._balance_cache:
            return self.t("leaderboard.no_balance_cache")
        results = sorted(
            self._balance_cache.values(), key=lambda x: x[1], reverse=True
        )[:top_n]
        if not results:
            return self.t("leaderboard.no_balance")
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, (qq_id, quota) in enumerate(results):
            rank = idx + 1
            prefix = medals[idx] if idx < 3 else f"{rank}."
            display = quota / ratio
            lines.append(f"{prefix} QQ:{qq_id} → {display:.6f}")
        return "\n".join(lines)

    async def _build_heist_board(self, top_n: int, ratio: int) -> str:
        """构建打劫排行榜：聚合打劫日志，按净收益排序（成功为正、失败为负）。"""
        query = """
            SELECT robber_qq_id,
                   COUNT(*) AS attempts,
                   SUM(CASE WHEN outcome IN ('SUCCESS', 'CRITICAL') THEN 1 ELSE 0 END) AS wins,
                   SUM(amount) AS net
            FROM daily_heist_log
            GROUP BY robber_qq_id
            ORDER BY net DESC
            LIMIT %s
        """
        rows = await self.core.execute_query(query, (top_n,), fetch='all')
        if not rows:
            return self.t("leaderboard.no_heist")

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, row in enumerate(rows):
            rank = idx + 1
            prefix = medals[idx] if idx < 3 else f"{rank}."
            net_display = (row['net'] or 0) / ratio
            lines.append(
                self.t("leaderboard.heist_line", prefix=prefix, qq=row['robber_qq_id'],
                       attempts=row['attempts'], wins=row['wins'], net=f"{net_display:.6f}")
            )
        return "\n".join(lines)

    # --- 绑定功能辅助方法 ---

    async def _check_self_binding(self, user_qq_id: int) -> Optional[str]:
        """检查用户QQ是否已绑定。"""
        if binding := await self.core.get_user_by_qq(user_qq_id):
            return self.t("bind.already_bound", site_id=binding['website_user_id'])
        return None

    async def _check_qq_level(self, event: AstrMessageEvent, user_qq_id: int) -> Optional[str]:
        binding_conf = config_get(self.config, 'binding_settings', {})
        min_level = binding_conf.get('min_qq_level', 16)
        try:
            stranger_info = await event.bot.get_stranger_info(user_id=user_qq_id, no_cache=True)

            raw_level = stranger_info.get('qqLevel') 

            if raw_level is not None:
                user_qq_level = int(raw_level)
                if user_qq_level < min_level:
                    return self.t("bind.qq_level_low", level=user_qq_level, min_level=min_level)
            else:
                logger.warning(f"无法从API获取用户 {user_qq_id} 的QQ等级，将跳过此项检查。" )
        except Exception as e:
            logger.warning(f"获取QQ等级失败，跳过检查: {e}", exc_info=True)
        return None

    async def _check_api_user_exists(self, website_user_id: int) -> Optional[str]:
        """检查网站用户ID是否存在。"""
        if not await self.core.get_api_user_data(website_user_id):
            return self.t("bind.api_user_not_found", site_id=website_user_id)
        return None

    async def _check_website_id_blacklist(self, website_user_id: int) -> Optional[str]:
        """检查网站ID是否在禁止绑定黑名单中（仅针对新增绑定，已绑定不受影响）。"""
        binding_conf = config_get(self.config, 'binding_settings', {})
        blacklist = binding_conf.get('forbidden_website_ids', [])
        forbidden_ids = set(int(i) for i in blacklist if str(i).lstrip('-').isdigit())
        if website_user_id in forbidden_ids:
            return self.t("bind.website_blacklisted", site_id=website_user_id)
        return None

    async def _check_user_blacklist(self, user_qq_id: int) -> Optional[str]:
        """检查用户QQ是否在禁止绑定黑名单中（仅针对新增绑定，已绑定不受影响）。"""
        binding_conf = config_get(self.config, 'binding_settings', {})
        blacklist = binding_conf.get('forbidden_user_ids', [])
        forbidden_ids = set(int(i) for i in blacklist if str(i).lstrip('-').isdigit())
        if user_qq_id in forbidden_ids:
            return self.t("bind.user_blacklisted", qq=user_qq_id)
        return None

    async def _check_id_uniqueness(self, website_user_id: int) -> Optional[str]:
        """检查网站用户ID是否已被他人绑定。"""
        if await self.core.get_user_by_website_id(website_user_id):
            return self.t("bind.id_taken", site_id=website_user_id)
        return None

    async def _perform_binding_ritual(self, user_qq_id: int, website_user_id: int) -> Tuple[bool, str]:
        """
        执行最终的绑定操作，包含数据库写入和API更新，失败时回滚。
        """
        inserted = False
        try:
            await self.core.insert_binding(user_qq_id, website_user_id)
            inserted = True
            
            api_user_data = await self.core.get_api_user_data(website_user_id)
            binding_conf = config_get(self.config, 'binding_settings', {})
            target_group = binding_conf.get('binding_group', 'default')
            
            if api_user_data:
                if api_user_data.get('group') == target_group:
                    # 【修复】用户已在目标组中，跳过无意义的 PUT，避免重绑时 no-op 更新被拒
                    logger.info(f"网站用户 {website_user_id} 已在目标组 {target_group} 中，跳过用户组更新。")
                else:
                    # 【修复】只发送后端允许修改的字段，避免 Invalid parameters
                    update_payload = {
                        "id": website_user_id,
                        "username": api_user_data.get("username"),
                        "display_name": api_user_data.get("display_name"),
                        "role": api_user_data.get("role"),
                        "status": api_user_data.get("status"),
                        "group": target_group
                    }
                    update_success = await self.core.update_api_user(update_payload)
                    if not update_success:
                        raise Exception("API group update failed.")
            else:
                raise Exception("API user data not found during binding ritual.")

            return True, self.t("bind.success", site_id=website_user_id, group=target_group)
        
        except Exception as e:
            logger.error(f"绑定仪式中发生错误: {e}", exc_info=True)
            if inserted:
                await self.core.delete_binding(qq_id=user_qq_id, website_user_id=website_user_id)
            return False, self.t("bind.failed")

    async def _perform_openid_binding(self, event, openid: str, website_user_id: int) -> str:
        """执行 OpenID 绑定。

        可配置开关：
          - openid_require_qq_bound：该网站 ID 须已绑有 QQ 号（野机），否则拒绝官机绑定；
          - wild_bind_group_only：用户组晋升仅由野机(QQ)绑定触发，官机(OpenID)绑定不改分组。
        """
        # 检查 OpenID 是否已被绑定
        existing = await self.core.get_user_by_openid(openid)
        if existing:
            return self.t("bind.already_bound", site_id=existing['website_user_id'])

        # 检查网站 ID 是否已被 OpenID 绑定
        already = await self.core.get_openid_by_website_id(website_user_id)
        if already:
            return self.t("bind.id_taken", site_id=website_user_id)

        # 检查网站用户是否存在
        if not await self.core.get_api_user_data(website_user_id):
            return self.t("bind.api_user_not_found", site_id=website_user_id)

        # 网站黑名单
        binding_conf = config_get(self.config, 'binding_settings', {})
        blacklist = binding_conf.get('forbidden_website_ids', [])
        forbidden_ids = set(int(i) for i in blacklist if str(i).lstrip('-').isdigit())
        if website_user_id in forbidden_ids:
            return self.t("bind.website_blacklisted", site_id=website_user_id)

        # 开关「官机绑定需先绑QQ」：该网站 ID 尚无野机(QQ)绑定时拒绝官机绑定
        if binding_conf.get('openid_require_qq_bound', False) \
           and not await self.core.get_user_by_website_id(website_user_id):
            return self.t("bind.openid_need_qq", site_id=website_user_id)

        # 开关「仅野机绑定改分组」：开启时官机(OpenID)绑定不做用户组晋升
        promote_group = not binding_conf.get('wild_bind_group_only', False)
        target_group = binding_conf.get('binding_group', 'default')

        inserted = False
        try:
            await self.core.insert_openid_binding(openid, website_user_id)
            inserted = True
            if promote_group:
                api_user_data = await self.core.get_api_user_data(website_user_id)
                if not api_user_data:
                    raise RuntimeError("API user data unavailable during OpenID binding")
                if api_user_data.get('group') != target_group:
                    update_payload = {
                        "id": website_user_id,
                        "username": api_user_data.get("username"),
                        "display_name": api_user_data.get("display_name"),
                        "role": api_user_data.get("role"),
                        "status": api_user_data.get("status"),
                        "group": target_group
                    }
                    update_success = await self.core.update_api_user(update_payload)
                    if not update_success:
                        raise Exception("API group update failed.")

            if promote_group:
                return self.t("bind.openid_success", openid=openid, site_id=website_user_id, group=target_group)
            # 官机绑定不改分组：成功提示中明确说明
            return self.t("bind.openid_success_no_group", openid=openid, site_id=website_user_id)
        except Exception as e:
            logger.error(f"OpenID 绑定失败: {e}", exc_info=True)
            if inserted:
                await self.core.delete_openid_binding(openid=openid, website_user_id=website_user_id)
            return self.t("bind.failed")

    async def _send_success_pm(self, event: AstrMessageEvent, user_qq_id: int, website_user_id: int):
        """如果配置允许，发送绑定成功私信。"""
        pm_conf = config_get(self.config, 'optional_pm_settings', {})
        if not pm_conf.get('enable_bind_success_pm'):
            return
        
        try:
            template = pm_conf.get('bind_success_pm_template', "绑定成功！")
            group = config_get(self.config, 'binding_settings.binding_group', 'default')

            user_nickname = str(user_qq_id)
            try:
                stranger_info = await event.bot.get_stranger_info(user_id=user_qq_id, no_cache=True)
                user_nickname = stranger_info.get("nickname", str(user_qq_id))
            except Exception as e:
                logger.warning(f"为私信模板获取QQ昵称失败: {e}", exc_info=True)

            site_username = self.t("common.unknown")
            api_user_data = await self.core.get_api_user_data(website_user_id)
            if api_user_data:
                site_username = api_user_data.get("username", self.t("common.unknown"))

            content = template.format(
                id=website_user_id,
                group=group,
                user_qq=user_qq_id,
                user_nickname=user_nickname,
                site_username=site_username
            )
            
            await event.bot.send_private_msg(user_id=user_qq_id, message=content)
            logger.info(f"成功发送绑定成功私信至 {user_qq_id}。" )
        except Exception as e:
            logger.error(f"发送绑定成功私信失败: {e}", exc_info=True)