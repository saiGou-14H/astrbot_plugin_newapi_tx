"""Upload generated PNGs to an explicitly enabled S3/MinIO prefix.

Only owned PNG bytes are uploaded; returned URLs contain no signed credentials.
Dependencies are lazy so disabled storage does not require the MinIO SDK. Disk
reads and synchronous SDK calls run in worker threads. Anonymous HEAD and GET
must both succeed before a URL is returned. Failures expose only stable codes.

A failed public check can leave an uploaded object behind. Cancellation cannot
stop an SDK call already running in a thread; lifecycle cleanup belongs to the
bucket administrator. This module never creates buckets or changes policies.
"""
import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from io import BytesIO
import ipaddress
from pathlib import Path
import re
import struct
from urllib.parse import urlsplit
from uuid import uuid4
import zlib

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 5 * 1024 * 1024


class ImageStorageError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _root_url(value, schemes):
    # Reject unsafe characters *before* urlsplit (which strips some controls).
    # A root-only ASCII URL also remains safe inside a Markdown link target.
    if not isinstance(value, str) or not re.fullmatch(
            r'https?://[A-Za-z0-9.\-:\[\]]+/?', value):
        raise ValueError
    parts = urlsplit(value)
    if (parts.scheme not in schemes or not parts.hostname
            or not re.fullmatch(r'(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::[0-9]+)?', parts.netloc)):
        raise ValueError
    host = parts.hostname
    if ':' in host:
        ipaddress.IPv6Address(host)
    elif not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                 for label in host.split('.')) or len(host) > 253:
        raise ValueError
    if parts.netloc.endswith(':') or (parts.port is not None and not 1 <= parts.port <= 65535):
        raise ValueError
    return parts


def _settings(config):
    if not isinstance(config, Mapping):
        raise ImageStorageError('invalid_configuration')
    enabled = config.get('enabled', False)
    if enabled is False:
        raise ImageStorageError('disabled')
    if enabled is not True:
        raise ImageStorageError('invalid_configuration')
    try:
        endpoint = _root_url(config.get('endpoint'), ('http', 'https'))
        public_parts = _root_url(config.get('public_base_url'), ('https',))
        bucket = config.get('bucket')
        prefix = config.get('prefix', 'reports')
        access_key = config.get('access_key')
        secret_key = config.get('secret_key')
        if (not isinstance(bucket, str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket)
                or '..' in bucket or '.-' in bucket or '-.' in bucket
                or re.fullmatch(r'\d+\.\d+\.\d+\.\d+', bucket)
                or not isinstance(prefix, str) or len(prefix) > 256
                or not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', prefix)
                or not isinstance(access_key, str) or not access_key.strip()
                or not isinstance(secret_key, str) or not secret_key.strip()):
            raise ValueError
    except (TypeError, ValueError):
        raise ImageStorageError('invalid_configuration') from None
    public = f'https://{public_parts.netloc}'
    return endpoint, public, bucket, prefix, access_key, secret_key


def _validate_png(data):
    """Check PNG framing, CRCs and basic structure, without decoding pixels."""
    if not data.startswith(PNG_MAGIC):
        raise ImageStorageError('invalid_png')
    offset = len(PNG_MAGIC)
    seen_header = seen_data = seen_palette = data_ended = False
    color = None
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], 'big')
        kind = data[offset + 4:offset + 8]
        end = offset + 8 + length
        if (end + 4 > len(data) or not re.fullmatch(b'[A-Za-z]{4}', kind)
                or kind[2] & 32
                or zlib.crc32(data[offset + 4:end]) != int.from_bytes(data[end:end + 4], 'big')):
            break
        chunk = data[offset + 8:end]
        if not seen_header and kind != b'IHDR':
            break
        if kind == b'IHDR':
            if seen_header or length != 13:
                break
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunk)
            depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if (not 0 < width <= 0x7fffffff or not 0 < height <= 0x7fffffff
                    or depth not in depths.get(color, ()) or compression or filtering
                    or interlace not in (0, 1)):
                break
            seen_header = True
        elif kind == b'PLTE':
            if seen_palette or seen_data or color in (0, 4) or not length or length % 3 or length > 768:
                break
            seen_palette = True
        elif kind == b'IDAT':
            if data_ended or (color == 3 and not seen_palette):
                break
            seen_data = True
        elif kind == b'IEND':
            if length == 0 and seen_data and end + 4 == len(data):
                return
            break
        elif not kind[0] & 32:  # Unknown critical chunk.
            break
        if seen_data and kind != b'IDAT':
            data_ended = True
        offset = end + 4
    raise ImageStorageError('invalid_png')


def _read_png(path):
    try:
        with Path(path).open('rb') as file:
            data = file.read(MAX_PNG_BYTES + 1)
    except (OSError, TypeError, ValueError):
        raise ImageStorageError('png_unreadable') from None
    if len(data) > MAX_PNG_BYTES:
        raise ImageStorageError('png_too_large')
    _validate_png(data)
    return data


def _put_png(settings, object_name, data):
    try:
        from minio import Minio
        import urllib3
    except ImportError:
        raise ImageStorageError('dependency_missing') from None
    endpoint, _, bucket, _, access_key, secret_key = settings
    pool = None
    try:
        pool = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=20), retries=False)
        client = Minio(endpoint.netloc, access_key=access_key, secret_key=secret_key,
                       secure=endpoint.scheme == 'https', region='us-east-1', http_client=pool)
        client.put_object(bucket, object_name, BytesIO(data), len(data), content_type='image/png')
    except Exception:
        # SDK exceptions may contain credentials, server responses or URLs.
        raise ImageStorageError('upload_failed') from None
    finally:
        if pool is not None:
            try:
                pool.clear()
            except Exception:
                pass  # Cleanup must not mask the stable outcome.


def _check_public_headers(response, size):
    media_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if response.status_code != 200 or media_type != 'image/png':
        raise ImageStorageError('public_image_unavailable')
    length = response.headers.get('content-length')
    if length is not None and (not re.fullmatch(r'[0-9]+', length) or int(length) != size):
        raise ImageStorageError('public_image_size_mismatch')


async def _verify_public(httpx, url, data):
    try:
        # No environment proxies/netrc credentials, cookies from earlier calls,
        # redirects or signed URL. Probe exactly what a public reader receives.
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False,
                                     headers={'Accept-Encoding': 'identity'}) as client:
            response = await client.head(url)
            _check_public_headers(response, len(data))
            client.cookies.clear()
            async with client.stream('GET', url) as response:
                _check_public_headers(response, len(data))
                offset = 0
                async for chunk in response.aiter_raw():
                    end = offset + len(chunk)
                    if end > len(data):
                        raise ImageStorageError('public_image_size_mismatch')
                    if chunk != data[offset:end]:
                        raise ImageStorageError('public_image_unavailable')
                    offset = end
                if offset != len(data):
                    raise ImageStorageError('public_image_size_mismatch')
    except ImageStorageError:
        raise
    except Exception:
        raise ImageStorageError('public_image_unavailable') from None


async def upload_tihudace_png(path, config) -> str:
    """Return an anonymously verified PNG URL, or raise ImageStorageError(code).

    Codes: disabled, invalid_configuration, dependency_missing, png_unreadable,
    png_too_large, invalid_png, upload_failed, public_image_unavailable,
    public_image_size_mismatch. Cancellation propagates normally.
    """
    settings = _settings(config)
    try:
        import httpx
    except ImportError:
        raise ImageStorageError('dependency_missing') from None
    data = await asyncio.to_thread(_read_png, path)
    _, public, bucket, prefix, _, _ = settings
    date = datetime.now(timezone.utc).strftime('%Y/%m/%d')
    object_name = f'{prefix}/{date}/{uuid4().hex}.png'
    await asyncio.to_thread(_put_png, settings, object_name, data)
    url = f'{public}/{bucket}/{object_name}'
    await _verify_public(httpx, url, data)
    return url
