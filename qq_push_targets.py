"""Resolve explicitly selected QQ groups without guessing official OpenIDs.

Numeric group numbers are aliases for admin-bound official sessions. They are
native IDs only for a single unambiguous OneBot adapter. Bindings alone never
subscribe a group: the configured target list remains the sending allowlist.
"""
import re

BINDINGS_KEY = 'tihudace_qq_group_bindings'
QQ_TYPES = frozenset(('qq_official', 'aiocqhttp'))
GROUP_TYPE = 'GroupMessage'


def is_group_number(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[1-9][0-9]{0,19}', value))


def _valid_part(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value))


def parse_group_session(value):
    if not isinstance(value, str):
        return None
    parts = value.split(':')
    if (len(parts) != 3 or parts[1] != GROUP_TYPE
            or not _valid_part(parts[0]) or not _valid_part(parts[2])):
        return None
    return parts[0], parts[2]


def route_session(route):
    return f"{route['platform_id']}:{GROUP_TYPE}:{route['group_id']}"


def configured_targets(conf):
    """Read editable strings and legacy objects from the same configuration key."""
    values = conf.get('push_targets', [])
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return [], 1
    targets, invalid = [], 0
    for item in values:
        if isinstance(item, dict):
            if not item.get('enabled', True):
                continue
            item = item.get('session') or item.get('group_id')
        if type(item) is int:
            item = str(item)
        if not isinstance(item, str):
            invalid += 1
            continue
        for value in re.split(r'[,，;；\s]+', item.strip()):
            if not value:
                continue
            if not is_group_number(value) and not parse_group_session(value):
                invalid += 1
                continue
            if value not in targets:
                targets.append(value)
    return targets, invalid


def current_group_route(event):
    """Use the real group identity, never a sender-isolated session suffix."""
    from astrbot.core.platform.message_type import MessageType
    kind = event.get_platform_name()
    if kind not in QQ_TYPES or event.get_message_type() != MessageType.GROUP_MESSAGE:
        return None
    group_id = str(event.get_group_id() or '')
    platform_id = str(event.get_platform_id() or '')
    if not _valid_part(platform_id) or not _valid_part(group_id):
        return None
    if kind == 'qq_official':
        from botpy.message import GroupMessage
        raw = getattr(event.message_obj, 'raw_message', None)
        if not isinstance(raw, GroupMessage) or str(raw.group_openid or '') != group_id:
            return None  # QQ guild channels also use GROUP_MESSAGE; do not bind them.
    elif not is_group_number(group_id):
        return None
    return {'platform_id': platform_id, 'platform_type': kind, 'group_id': group_id}


def resolve_targets(conf, bindings, platforms):
    """Return deduplicated routes and (target, stable-error-code) diagnostics."""
    selected, invalid = configured_targets(conf)
    issues = [('', 'invalid_configuration')] * invalid
    registered = {}
    for platform in platforms:
        meta = platform.meta()
        if meta.name in QQ_TYPES:
            registered.setdefault(str(meta.id), []).append(meta.name)
    bindings = bindings if isinstance(bindings, dict) else {}
    routes, seen = [], set()
    for target in selected:
        parsed = parse_group_session(target)
        route = None
        if parsed:
            platform_id, group_id = parsed
            kinds = registered.get(platform_id, [])
            if len(kinds) == 1:
                route = dict(platform_id=platform_id, platform_type=kinds[0], group_id=group_id)
            else:
                issues.append((target, 'platform_unavailable'))
                continue
        elif target in bindings:
            saved = bindings[target]
            if (not isinstance(saved, dict) or saved.get('platform_type') not in QQ_TYPES
                    or not _valid_part(saved.get('platform_id'))
                    or not _valid_part(saved.get('group_id'))):
                issues.append((target, 'invalid_binding'))
                continue
            route = {key: saved[key] for key in ('platform_id', 'platform_type', 'group_id')}
            if registered.get(route['platform_id']) != [route['platform_type']]:
                issues.append((target, 'platform_unavailable'))
                continue
        elif len(registered) == 1 and next(iter(registered.values())) == ['aiocqhttp']:
            route = dict(platform_id=next(iter(registered)), platform_type='aiocqhttp', group_id=target)
        else:
            issues.append((target, 'unbound_group_number'))
            continue
        if route['platform_type'] == 'aiocqhttp' and not is_group_number(route['group_id']):
            issues.append((target, 'invalid_group_id'))
            continue
        key = (route['platform_id'], route['group_id'])
        if key not in seen:
            routes.append(route)
            seen.add(key)
    return routes, issues
