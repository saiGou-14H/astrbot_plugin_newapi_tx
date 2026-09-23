"""Recover only explicit QQ member identities; never infer from author/nickname."""


def official_mention_ids(raw_message, self_id=""):
    data = raw_message if isinstance(raw_message, dict) else getattr(raw_message, "raw_data", None)
    mentions = data.get("mentions") if isinstance(data, dict) else None
    if not isinstance(mentions, (list, tuple)):
        mentions = getattr(raw_message, "mentions", None)
    if not isinstance(mentions, (list, tuple)):
        return []
    blocked = {str(self_id), "qq_official", "all", "0", ""}
    entries = []
    for mention in mentions:
        def field(name):
            return mention.get(name) if isinstance(mention, dict) else getattr(mention, name, None)
        aliases = [value.strip() for name in ("member_openid", "user_openid", "id")
                   if isinstance(value := field(name), str) and value.strip()]
        if field("is_you") is True or field("bot") is True:
            blocked.update(aliases)
        elif aliases:
            entries.append(aliases)
    result = []
    for aliases in entries:
        if any(alias in blocked for alias in aliases):
            continue
        identity = aliases[0]
        if identity not in result:
            result.append(identity)
    return result
