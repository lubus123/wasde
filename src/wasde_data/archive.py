"""Immutable raw archive of release files.

Layout: data/raw/releases/{YYYY-MM}/wasde-YYYY-MM-DD[-vN].{ext}
        data/raw/releases/{YYYY-MM}/manifest.jsonl    # one line per archived file

Existing files are never rewritten (CORE_PRINCIPLES #1): an archive pass skips
paths that already exist, so backfills are resumable and re-runs are no-ops.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from wasde_data.esmis import Release
from wasde_data.http_cache import _get


@dataclass
class ArchivedFile:
    release_id: str
    ext: str
    url: str
    local_path: Path
    sha256: str
    bytes: int
    skipped: bool  # already on disk


def _download(url: str, timeout: float = 180.0) -> bytes:
    return _get(url, None, None, timeout)


def archive_release(release: Release, releases_dir: Path,
                    sleep_seconds: float = 1.0,
                    download=_download) -> list[ArchivedFile]:
    """Download every file of a release into the immutable layout. Idempotent."""
    month_dir = releases_dir / f"{release.release_datetime:%Y-%m}"
    month_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = month_dir / "manifest.jsonl"
    out: list[ArchivedFile] = []

    for url in release.files:
        ext = url.rsplit(".", 1)[-1].lower()
        target = month_dir / f"{release.release_id}.{ext}"
        if target.exists():
            payload = target.read_bytes()
            out.append(ArchivedFile(release.release_id, ext, url, target,
                                    hashlib.sha256(payload).hexdigest(),
                                    len(payload), skipped=True))
            continue
        payload = download(url)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(payload)
        tmp.replace(target)
        rec = ArchivedFile(release.release_id, ext, url, target,
                           hashlib.sha256(payload).hexdigest(),
                           len(payload), skipped=False)
        out.append(rec)
        with manifest_path.open("a") as fh:
            fh.write(json.dumps({
                "release_id": rec.release_id, "esmis_id": release.esmis_id,
                "ext": rec.ext, "url": rec.url, "sha256": rec.sha256,
                "bytes": rec.bytes,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }) + "\n")
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return out
