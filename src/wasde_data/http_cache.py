"""Shared HTTP layer: every fetch is retried on transient failure and cached on disk.

The cache key is SHA1(url + sorted params), so identical requests are free on
re-run — backfills are resumable and polite to USDA by construction.
(Pattern lifted from dairy-model, which battle-tested it against USDA endpoints.)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

_TRANSIENT_NETWORK_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.PoolTimeout,
)


class TransientHTTPError(Exception):
    """5xx or 429 — worth retrying."""


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, (TransientHTTPError, *_TRANSIENT_NETWORK_ERRORS))


def cache_key(url: str, params: dict | None) -> str:
    payload = url + json.dumps(params or {}, sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


@retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(8),
    wait=wait_exponential(multiplier=2, min=2, max=150),
    reraise=True,
)
def _get(url: str, params: dict | None, headers: dict | None, timeout: float) -> bytes:
    resp = httpx.get(url, params=params, headers=headers, timeout=timeout,
                     follow_redirects=True)
    if resp.status_code >= 500 or resp.status_code == 429:
        raise TransientHTTPError(f"HTTP {resp.status_code} from {url}")
    resp.raise_for_status()
    return resp.content


def cached_get_bytes(
    url: str,
    cache_dir: Path,
    params: dict | None = None,
    headers: dict | None = None,
    suffix: str = ".json",
    force: bool = False,
    timeout: float = 120.0,
) -> bytes:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{cache_key(url, params)}{suffix}"
    if path.exists() and not force:
        return path.read_bytes()
    content = _get(url, params, headers, timeout)
    path.write_bytes(content)
    return content


def cached_get_json(
    url: str,
    cache_dir: Path,
    params: dict | None = None,
    headers: dict | None = None,
    force: bool = False,
    timeout: float = 120.0,
):
    raw = cached_get_bytes(url, cache_dir, params=params, headers=headers,
                           suffix=".json", force=force, timeout=timeout)
    return json.loads(raw)
