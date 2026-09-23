"""Recover official QQ member mentions retained by the AstrBot adapter.

GROUP_AT_MESSAGE_CREATE carries member OpenIDs in top-level `mentions`.
AstrBot 4.28.1 retains raw_data but only emits an At component for the bot.
Never infer a target from a nickname or from a quoted message's mentions.
"""


def official_mention_ids(raw_message, self_id=""):
    data = raw_message if isinstance(raw_message, dict) else getattr(raw_message, "raw_data", None)
    mentions = data.get("mentions") if isinstance(data, dict) else None
    if not isinstance(mentions, (list, tuple)):
        mentions = getattr(raw_message, "mentions", None)
    if not isinstance(mentions, (list, tuple)):
        return []
    result = []
    for mention in mentions:
        def field(name):
            return mention.get(name) if isinstance(mention, dict) else getattr(mention, name, None)
        if field("is_you") is True or field("bot") is True:
            continue
        identity = field("member_openid") or field("user_openid") or field("id")
        if not isinstance(identity, str):
            continue
        identity = identity.strip()
        if not identity or identity in (str(self_id), "qq_official", "all", "0"):
            continue
        if identity not in result:
            result.append(identity)
    return result
