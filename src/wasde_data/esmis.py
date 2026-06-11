"""ESMIS catalog client.

ESMIS (esmis.nal.usda.gov, the relocated Cornell usda.library.cornell.edu) exposes a
free, no-auth JSON API listing every WASDE release with absolute file URLs:

    GET /api/v1/release/findByIdentifier/{identifier}?page=N   # 25 results per page

Release identity: ESMIS gives one entry per release with a numeric id. Revised
releases carry 'vN' in their filenames (wasde0526v2.xml); we surface that as
version so a revision becomes its own release_id ('wasde-2026-05-12-v2') and the
prior vintage is preserved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from wasde_data.http_cache import cached_get_json

_VERSION_RE = re.compile(r"v(\d+)\.[a-z]+$", re.IGNORECASE)

# Era classification by best available format. ZIPs (a few mid-90s releases)
# contain the TXT.
_ERA_BY_EXT = [("xml", "xml"), ("txt", "txt"), ("zip", "txt"), ("pdf", "pdf_scan")]


@dataclass(frozen=True)
class Release:
    esmis_id: str
    title: str
    release_datetime: datetime
    files: tuple[str, ...] = field(default=())

    @property
    def version(self) -> int:
        versions = [int(m.group(1)) for f in self.files if (m := _VERSION_RE.search(f))]
        return max(versions, default=1)

    @property
    def release_id(self) -> str:
        base = f"wasde-{self.release_datetime:%Y-%m-%d}"
        return base if self.version == 1 else f"{base}-v{self.version}"

    @property
    def report_month(self) -> str:
        return f"{self.release_datetime:%Y-%m}-01"

    @property
    def format_era(self) -> str:
        exts = {f.rsplit(".", 1)[-1].lower() for f in self.files}
        for ext, era in _ERA_BY_EXT:
            if ext in exts:
                return era
        return "unknown"

    def file_for(self, ext: str) -> str | None:
        for f in self.files:
            if f.rsplit(".", 1)[-1].lower() == ext:
                return f
        return None


def parse_release(entry: dict) -> Release:
    dt = datetime.strptime(entry["release_datetime"], "%Y-%m-%dT%H:%M:%S%z")
    return Release(
        esmis_id=str(entry["id"]),
        title=entry.get("title", ""),
        release_datetime=dt.replace(tzinfo=None),
        files=tuple(entry.get("files", [])),
    )


def latest_release_ids(releases: list[Release]) -> set[str]:
    """One winner per report_month: highest (version, release_datetime)."""
    best: dict[str, Release] = {}
    for r in releases:
        cur = best.get(r.report_month)
        if cur is None or (r.version, r.release_datetime) > (cur.version, cur.release_datetime):
            best[r.report_month] = r
    return {r.release_id for r in best.values()}


class EsmisClient:
    def __init__(self, base_url: str, cache_dir: Path, identifier: str = "wasde"):
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir
        self.identifier = identifier

    def catalog_page(self, page: int, force: bool = False) -> dict:
        url = f"{self.base_url}/api/v1/release/findByIdentifier/{self.identifier}"
        return cached_get_json(url, self.cache_dir, params={"page": page}, force=force)

    def all_releases(self, force_first_page: bool = False) -> list[Release]:
        """Walk every catalog page. Page 0 is fetched fresh when force_first_page
        (update runs must see new releases); deeper pages are immutable history
        and stay cached — but pagination shifts as new releases prepend, so a
        backfill should pass force_first_page=False and rely on dedup by esmis_id."""
        first = self.catalog_page(0, force=force_first_page)
        total_pages = first["pager"]["total_pages"]
        releases = [parse_release(e) for e in first["results"]]
        for page in range(1, total_pages):
            data = self.catalog_page(page)
            releases.extend(parse_release(e) for e in data["results"])
        seen: set[str] = set()
        unique = []
        for r in releases:  # pagination overlap dedup, newest entry wins
            if r.esmis_id not in seen:
                seen.add(r.esmis_id)
                unique.append(r)
        return unique
