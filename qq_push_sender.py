"""Send to already resolved QQ groups; never infer an official OpenID.

Official True means a nonempty message receipt ID was returned (not delivery
confirmation). OneBot True means only that Context accepted the adapter call.
The official upload uses the same SDK Route as AstrBot 4.28.1 because its public
post_group_file accepts URLs only, not local base64 file_data.
"""
import asyncio
import base64
import logging
from pathlib import Path
import secrets

logger = logging.getLogger(__name__)


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


async def send_qq_group_image(context, route: dict, summary: str, png: str) -> bool:
    """Send one image to a trusted, resolved route; contain per-target errors.

    Caller owns authorization, target allowlisting and alias resolution. This
    function does not prove a numeric alias corresponds to an official group.
    CancelledError propagates so shutdown does not turn into a failed send.
    """
    stage = "route"
    try:
        if not isinstance(route, dict) or not isinstance(summary, str):
            return False
        platform_id = route.get("platform_id")
        kind = route.get("platform_type")
        group_id = route.get("group_id")
        if (kind not in ("qq_official", "aiocqhttp")
                or not _nonempty_string(platform_id)
                or not _nonempty_string(group_id)):
            return False
        platform = context.get_platform_inst(platform_id)
        if platform is None:
            logger.warning("QQ push: platform_unavailable")
            return False
        meta = platform.meta()
        if meta.id != platform_id or meta.name != kind:
            logger.warning("QQ push: platform_mismatch")
            return False

        if kind == "aiocqhttp":
            if not group_id.isascii() or not group_id.isdecimal():
                return False
            from astrbot.api.event import MessageChain
            from astrbot.core.platform.message_session import MessageSession
            from astrbot.core.platform.message_type import MessageType

            stage = "adapter"
            session = MessageSession(platform_id, MessageType.GROUP_MESSAGE, group_id)
            chain = MessageChain().message(summary).file_image(png)
            accepted = await context.send_message(session, chain)
            if accepted is True:
                logger.info("QQ push: OneBot adapter call completed; no QQ receipt verified")
                return True
            logger.warning("QQ push: adapter_not_accepted")
            return False

        stage = "image_read"
        image_bytes = await asyncio.to_thread(Path(png).read_bytes)
        if not image_bytes:
            logger.warning("QQ push: empty_image")
            return False
        file_data = base64.b64encode(image_bytes).decode("ascii")
        from botpy.http import Route

        api = platform.client.api
        stage = "upload"
        media_response = await api._http.request(
            Route("POST", "/v2/groups/{group_openid}/files", group_openid=group_id),
            json={"group_openid": group_id, "file_type": 1,
                  "file_data": file_data, "srv_send_msg": False},
        )
        if (not isinstance(media_response, dict)
                or not _nonempty_string(media_response.get("file_info"))
                or not _nonempty_string(media_response.get("file_uuid"))):
            logger.warning("QQ push: invalid_upload_receipt")
            return False
        # Only pass documented media fields, not arbitrary server response data.
        media = {"file_info": media_response["file_info"],
                 "file_uuid": media_response["file_uuid"]}
        stage = "message"
        receipt = await api.post_group_message(
            group_openid=group_id, msg_type=7, content=summary,
            media=media, msg_seq=secrets.randbelow(10000) + 1,
        )
        if not isinstance(receipt, dict) or not _nonempty_string(receipt.get("id")):
            logger.warning("QQ push: missing_message_receipt")
            return False
        logger.info("QQ push: official message receipt verified")
        return True
    except Exception:
        # Do not log exception text, payload, paths, IDs or SDK response objects.
        logger.warning("QQ push: failed at %s", stage)
        return False
