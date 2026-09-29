"""DingTalk custom-robot signing. No credentials or URLs are logged."""
import base64
import hashlib
import hmac
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def signed_webhook_url(webhook: str, secret: str = "", *, timestamp_ms=None) -> str:
    """Sign timestamp + newline + secret; URL-encode base64 exactly once.

    Empty secret preserves keyword/IP-only robot configuration. Signing is
    performed for each send, never saved as a reusable signed webhook URL.
    """
    secret = str(secret or "").strip()
    if not secret:
        return webhook
    parsed = urlsplit(webhook)
    if parsed.scheme != "https" or parsed.hostname != "oapi.dingtalk.com" or parsed.path != "/robot/send":
        raise ValueError("Invalid DingTalk custom-robot endpoint")
    params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
              if k not in ("timestamp", "sign")]
    if not any(k == "access_token" and v for k, v in params):
        raise ValueError("DingTalk access token is missing")
    timestamp = str(time.time_ns() // 1_000_000 if timestamp_ms is None else int(timestamp_ms))
    digest = hmac.new(secret.encode("utf-8"),
                      f"{timestamp}\n{secret}".encode("utf-8"),
                      hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode("ascii")
    params.extend([("timestamp", timestamp), ("sign", signature)])
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ""))
