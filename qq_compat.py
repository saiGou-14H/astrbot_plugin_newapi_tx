"""QQ member normalization, command-only wake gates and safe diagnostics.

AstrBot 4.28.1 already registers SDK group parsers. Instance hooks normalize
members before submission; reversible synchronous command-filter hooks admit
bare group commands without permanently opening the default LLM wake gate.
"""
import asyncio
import contextvars
import re
import time
from types import SimpleNamespace
from functools import wraps

from astrbot.api.message_components import At, Plain
from .qq_mentions import official_mention_ids

_MARKUP = re.compile(r'<@!?([A-Za-z0-9_-]+)>|<qqbot-at-user\s+id=[\"\']([A-Za-z0-9_-]+)[\"\']\s*/>')
_COMMAND = re.compile(r'^/?(提及诊断|调整余额|查余额|查询|打劫)(?=\s|<|$)')
_CONTEXT = contextvars.ContextVar('newapi_qq_mention_context', default=None)
_MISSING = object()


def is_bare_group_command(event, command_filter):
    """Admit only exact registered commands from SDK-backed QQ group events."""
    from astrbot.api.message_components import AtAll, Reply
    from astrbot.core.platform.message_type import MessageType
    from botpy.message import GroupMessage

    if event.get_platform_name() != 'qq_official' or event.is_at_or_wake_command:
        return False
    message = event.message_obj
    raw = getattr(message, 'raw_message', None)
    if (message.type != MessageType.GROUP_MESSAGE
            or not isinstance(raw, GroupMessage)
            or not raw.group_openid
            or str(raw.group_openid) != str(event.get_group_id())):
        return False
    # Keep native mention/reply semantics. Member mentions are valid arguments,
    # but a leading mention of another member must not address this bot.
    segments = event.get_messages()
    if segments and isinstance(segments[0], At):
        return False
    if any(isinstance(item, (AtAll, Reply)) or (
            isinstance(item, At) and str(item.qq) in (str(event.get_self_id()), 'all')
    ) for item in segments):
        return False
    text = re.sub(r'\s+', ' ', event.get_message_str().strip())
    return any(name and (text == name or text.startswith(name + ' '))
               for name in command_filter.get_complete_command_names())


def raw_data(raw):
    data = raw if isinstance(raw, dict) else getattr(raw, 'raw_data', None)
    return data if isinstance(data, dict) else {}


def known_mentions(raw, self_id=''):
    data = raw_data(raw)
    mentions = data.get('mentions')
    if not isinstance(mentions, (list, tuple)):
        mentions = getattr(raw, 'mentions', ())
    if not isinstance(mentions, (list, tuple)):
        mentions = ()
    blocked = {str(self_id), 'qq_official', 'all', '0', ''}
    entries = []
    for entry in mentions:
        get = entry.get if isinstance(entry, dict) else lambda k: getattr(entry, k, None)
        aliases = [v.strip() for k in ('member_openid', 'user_openid', 'id')
                   if isinstance(v := get(k), str) and v.strip()]
        if get('is_you') is True or get('bot') is True:
            blocked.update(aliases)
        elif aliases:
            entries.append(aliases)
    mapping = {}
    for aliases in entries:
        if any(alias in blocked for alias in aliases):
            continue
        for alias in aliases:
            # Conflicting aliases stay unresolved; never choose an arbitrary account.
            mapping[alias] = aliases[0] if alias not in mapping or mapping[alias] == aliases[0] else None
    return blocked, mapping


def command_name(raw):
    data = raw_data(raw)
    content = data.get('content', getattr(raw, 'content', ''))
    if not isinstance(content, str):
        return None
    blocked, _ = known_mentions(raw)
    text = _MARKUP.sub(lambda m: ' ' if (m[1] or m[2]) in blocked else m[0], content).strip()
    match = _COMMAND.match(text)
    return match[1] if match else None


def mention_summary(raw):
    data = raw_data(raw)
    mentions = data.get('mentions')
    sdk = getattr(raw, 'mentions', None)
    content = data.get('content', '')
    return {
        'raw_available': isinstance(raw, dict) or isinstance(getattr(raw, 'raw_data', None), dict),
        'raw_mentions_present': 'mentions' in data,
        'raw_mentions_count': len(mentions) if isinstance(mentions, (list, tuple)) else 0,
        'sdk_mentions_count': len(sdk) if isinstance(sdk, (list, tuple)) else 0,
        'content_markup_count': len(_MARKUP.findall(content)) if isinstance(content, str) else 0,
        'raw_member_count': len(official_mention_ids(raw)),
    }


def normalize_members(message):
    """Use only identities supplied in top-level mentions; leave raw data intact."""
    raw = message.raw_message
    targets = official_mention_ids(raw, message.self_id)
    blocked, aliases = known_mentions(raw, message.self_id)
    targets = [target for target in targets if target not in blocked]
    existing = {str(x.qq) for x in message.message if isinstance(x, At)}
    added = 0
    for target in targets:
        if target not in existing:
            component = At(qq=target)
            # AstrBot's int|str model coerces decimal strings to int at construction.
            # OpenIDs are opaque strings: preserve leading zeros before submission.
            component.qq = target
            message.message.append(component)
            existing.add(target)
            added += 1
    def clean(text):
        return _MARKUP.sub(lambda m: ' ' if (m[1] or m[2]) in blocked or aliases.get(m[1] or m[2]) in targets else m[0], text)
    # Strip only verified tags from derived text so CommandFilter can parse amounts
    # and single-argument queries. SDK content and raw_data remain untouched.
    message.message_str = clean(message.message_str).strip()
    for segment in message.message:
        if isinstance(segment, Plain):
            segment.text = clean(segment.text)
    return added


class QQMentionCompat:
    def __init__(self, logger):
        self.logger = logger
        self.active = True
        self.patches = []
        self.clients = []
        self.sequence = 0
        self.logged = 0
        self.probe_tasks = set()
        self.probe_after = 0.0
        self.group_probe_times = {}
        self.wake_patches = []
        self.bare_filter_matches = 0
        self.group_event_counts = {}

    def install_bare_command_wake(self):
        """Temporarily open native command gates, never the default LLM gate."""
        from astrbot.core.star.filter.command import CommandFilter
        from astrbot.core.star.filter.command_group import CommandGroupFilter
        if not self.active or self.wake_patches:
            return False

        def wrap_filter(original):
            @wraps(original)
            def command_filter(filter_obj, event, config):
                if not self.active or not is_bare_group_command(event, filter_obj):
                    return original(filter_obj, event, config)
                previous = event.is_at_or_wake_command
                event.is_at_or_wake_command = True
                try:
                    # Synchronous, with no awaits: no other task sees a fake wake.
                    # Native custom filters, parsing and exceptions stay intact.
                    matched = original(filter_obj, event, config)
                    if matched:
                        self.bare_filter_matches += 1
                        if self.bare_filter_matches <= 20:
                            self.logger.info(
                                '[NewAPI QQCommand] bare_filter_matched=True '
                                f'count={self.bare_filter_matches}'
                            )
                    return matched
                finally:
                    event.is_at_or_wake_command = previous
            return command_filter

        for filter_class in (CommandFilter, CommandGroupFilter):
            original = filter_class.filter
            wrapper = wrap_filter(original)
            self.wake_patches.append((filter_class, original, wrapper))
            filter_class.filter = wrapper
        self.logger.info('[NewAPI QQCommand] bare group command filters enabled')
        return True

    def probe_missing_target(self, event):
        """Observe the current group after a failed target lookup; never send a reply."""
        from botpy.message import GroupMessage
        raw = getattr(getattr(event, 'message_obj', None), 'raw_message', None)
        client = getattr(event, 'bot', None)
        if not self.active or not isinstance(raw, GroupMessage) or not any(x is client for x in self.clients):
            return
        auth = getattr(getattr(client, 'http', None), '_token', None)
        if not getattr(auth, 'access_token', None):
            return
        group = raw.group_openid
        if not group:
            return
        key = (id(client), group)
        now = time.monotonic()
        if self.probe_tasks or now < self.probe_after or now - self.group_probe_times.get(key, float('-inf')) < 30:
            return
        self.probe_after = now + 10
        if len(self.group_probe_times) >= 30 and key not in self.group_probe_times:
            self.group_probe_times.pop(next(iter(self.group_probe_times)))
        self.group_probe_times[key] = now
        observed = getattr(event.message_obj, '_newapi_mention_diagnostic', {})
        seq = observed.get('sequence', 0)
        seq = seq if type(seq) is int else 0
        # Keep only references needed for this GET, not the command event or account state.
        probe_event = SimpleNamespace(bot=client, message_obj=SimpleNamespace(raw_message=raw))

        async def probe():
            try:
                result = await query_group_receive_mode(probe_event)
                code = next((mode for mode in ('only_mention', 'mention_and_context', 'all')
                             if result.startswith(mode + '（')), None)
                if code is None:
                    code = 'permission_denied_11253' if '11253' in result else 'unavailable'
            except asyncio.CancelledError:
                raise
            except Exception:
                code = 'unavailable'
            if self.active:
                self.logger.info(f'[NewAPI QQState] sequence={seq} receive_mode={code}')

        task = asyncio.create_task(probe())
        self.probe_tasks.add(task)
        task.add_done_callback(self.probe_tasks.discard)

    async def aclose(self):
        tasks = tuple(self.probe_tasks)
        self.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _set(self, client, name, wrapper):
        previous = client.__dict__.get(name, _MISSING)
        self.patches.append((client, name, previous, wrapper))
        setattr(client, name, wrapper)

    def install(self, client):
        from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import botClient
        from botpy.message import GroupMessage
        if not self.active or not isinstance(client, botClient) or any(x is client for x in self.clients):
            return False
        self.clients.append(client)
        for name, mode in (('on_group_at_message_create', 'GROUP_AT_MESSAGE_CREATE'),
                           ('on_group_message_create', 'GROUP_MESSAGE_CREATE')):
            original = getattr(client, name)
            def build_callback(original, mode):
                @wraps(original)
                async def callback(raw):
                    if not self.active or not isinstance(raw, GroupMessage):
                        return await original(raw)
                    if self.wake_patches:
                        count = self.group_event_counts.get(mode, 0) + 1
                        self.group_event_counts[mode] = count
                        if count in (1, 2, 3, 10, 100, 1000):
                            self.logger.info(
                                f'[NewAPI QQReceive] event={mode} count={count}'
                            )
                    if command_name(raw) is None:
                        return await original(raw)
                    self.sequence += 1
                    token = _CONTEXT.set((client, raw, mode, self.sequence))
                    try:
                        return await original(raw)
                    finally:
                        _CONTEXT.reset(token)
                return callback
            self._set(client, name, build_callback(original, mode))
        original_commit = client._commit
        @wraps(original_commit)
        def commit(message):
            raw = message.raw_message
            if self.active and isinstance(raw, GroupMessage) and command_name(raw) is not None:
                before = sum(isinstance(x, At) and str(x.qq) != str(message.self_id)
                             and str(x.qq) != 'qq_official' for x in message.message)
                added = normalize_members(message)
                context = _CONTEXT.get()
                mode = context[2] if context and context[0] is client and context[1] is raw else 'unknown'
                seq = context[3] if context and context[0] is client and context[1] is raw else 0
                summary = mention_summary(raw)
                summary.update(event=mode, sequence=seq, member_at_before=before, member_at_added=added)
                # This owned dictionary contains fixed enum values, counts and booleans only.
                message._newapi_mention_diagnostic = summary
                if self.logged < 60:
                    self.logged += 1
                    fields = ' '.join(f'{key}={value}' for key, value in summary.items())
                    self.logger.info('[NewAPI QQMention] ' + fields)
            return original_commit(message)
        self._set(client, '_commit', commit)
        self.logger.info('[NewAPI QQMention] scoped hooks installed; native SDK routing unchanged')
        return True

    def close(self):
        self.active = False
        for task in tuple(self.probe_tasks):
            task.cancel()
        self.group_probe_times.clear()
        for filter_class, original, wrapper in reversed(self.wake_patches):
            if filter_class.filter is wrapper:
                filter_class.filter = original
        self.wake_patches.clear()
        for client, name, previous, wrapper in reversed(self.patches):
            if getattr(client, name, None) is wrapper:
                if previous is _MISSING:
                    delattr(client, name)
                else:
                    setattr(client, name, previous)
        self.patches.clear()
        self.clients.clear()


async def query_group_receive_mode(event):
    """One authenticated GET with existing SDK auth; no SDK payload/error logging."""
    from urllib.parse import quote
    import aiohttp
    from botpy.message import GroupMessage
    raw = getattr(event.message_obj, 'raw_message', None)
    if not isinstance(raw, GroupMessage):
        return '不适用于当前消息类型'
    client = getattr(event, 'bot', None)
    token = getattr(getattr(client, 'http', None), '_token', None)
    group = getattr(raw, 'group_openid', None)
    # Do not initiate a new login or refresh auth just for diagnostics.
    if token is None or not getattr(token, 'access_token', None) or not group:
        return '无法查询（当前连接没有可用凭据或群标识）'
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.get(
                'https://api.sgroup.qq.com/v2/groups/' + quote(str(group), safe='') + '/bot_state',
                headers={'Authorization': token.get_string(), 'X-Union-Appid': str(token.app_id)},
                allow_redirects=False,
            ) as response:
                data = await response.json(content_type=None)
                if not isinstance(data, dict):
                    return '无法查询（响应格式异常）'
                if data.get('code') in (11253, '11253'):
                    return '无法查询（11253：无此接口权限，不代表全量接收关闭）'
                if response.status != 200:
                    return '无法查询（QQ 接口拒绝或请求失败）'
                mode = data.get('recv_msg_setting')
                return {'all': 'all（接收所有消息）', 'only_mention': 'only_mention（仅提及）',
                        'mention_and_context': 'mention_and_context（提及及上下文）'}.get(mode, '未知（接口未返回已知模式）')
    except Exception:
        return '无法查询（网络或响应异常；未输出敏感错误内容）'
