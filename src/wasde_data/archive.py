"""Immutable raw archive of release files.

Layout: data/raw/releases/{YYYY-MM}/wasde-YYYY-MM-DD[-vN].{ext}
        data/raw/releases/{YYYY-MM}/manifest.jsonl    # one line per archived file

Existing files are never rewritten (CORE_PRINCIPLES #1): an archive pass skips
paths that already exist, so backfills are resumable and re-runs are no-ops.
"""

from __future__ import annotations

import hashlib
import json
import re
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
    skipped: bool        # already on disk
    canonical: bool = True  # the main report file for its ext (vs supplement)


def _download(url: str, timeout: float = 180.0) -> bytes:
    return _get(url, None, None, timeout)


# the main report file, as opposed to supplements ('_SpecialReport',
# '_China_revision') and revision variants ('ORIGwasde', 'R1wasde')
_CANONICAL_BASENAME = re.compile(
    r"^(wasde-\d{2}-\d{2}-\d{4}(v\d+)?|wasde\d{4}(v\d+)?|latest)$", re.IGNORECASE)


def _plan_targets(release: Release, month_dir: Path) -> list[tuple[str, str, Path, bool]]:
    """(url, ext, target_path, is_canonical) per file. One canonical file per
    ext — the one whose basename matches the main-report pattern (several 2001-13
    releases bundle supplement/revision files of the same extension)."""
    by_ext: dict[str, list[str]] = {}
    for url in release.files:
        by_ext.setdefault(url.rsplit(".", 1)[-1].lower(), []).append(url)
    def _is_canon(u: str) -> bool:
        return bool(_CANONICAL_BASENAME.match(u.rsplit("/", 1)[-1].rsplit(".", 1)[0]))

    plan = []
    for ext, ext_urls in by_ext.items():
        ordered = sorted(ext_urls, key=lambda u: (not _is_canon(u), u))
        for i, url in enumerate(ordered):
            if i == 0 and _is_canon(url):
                plan.append((url, ext, month_dir / f"{release.release_id}.{ext}", True))
            else:  # supplement/revision variant — or a release with no main file
                base = url.rsplit("/", 1)[-1]
                plan.append((url, ext,
                             month_dir / f"{release.release_id}__{base}", False))
    return plan


def archive_release(release: Release, releases_dir: Path,
                    sleep_seconds: float = 1.0,
                    download=_download) -> list[ArchivedFile]:
    """Download every file of a release into the immutable layout. Idempotent.
    Only canonical files (one per ext) are returned for release_files; extras
    land on disk + manifest under '{release_id}__{original_name}'."""
    month_dir = releases_dir / f"{release.release_datetime:%Y-%m}"
    month_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = month_dir / "manifest.jsonl"
    out: list[ArchivedFile] = []

    for url, ext, target, is_canonical in _plan_targets(release, month_dir):
        if target.exists():
            payload = target.read_bytes()
            out.append(ArchivedFile(release.release_id, ext, url, target,
                                    hashlib.sha256(payload).hexdigest(),
                                    len(payload), skipped=True,
                                    canonical=is_canonical))
            continue
        payload = download(url)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(payload)
        tmp.replace(target)
        rec = ArchivedFile(release.release_id, ext, url, target,
                           hashlib.sha256(payload).hexdigest(),
                           len(payload), skipped=False, canonical=is_canonical)
        out.append(rec)
        with manifest_path.open("a") as fh:
            fh.write(json.dumps({
                "release_id": rec.release_id, "esmis_id": release.esmis_id,
                "ext": rec.ext, "url": rec.url, "sha256": rec.sha256,
                "bytes": rec.bytes, "canonical": is_canonical,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }) + "\n")
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return out
