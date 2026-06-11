"""Backfill the full ESMIS WASDE catalog and raw file archive.

Resumable: catalog pages are HTTP-cached, already-archived files are skipped.
Run with --refresh-catalog to re-pull the catalog (e.g. after a new release).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wasde_data import db
from wasde_data.archive import archive_release
from wasde_data.config import load_config
from wasde_data.esmis import EsmisClient, latest_release_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-catalog", action="store_true",
                        help="bypass the HTTP cache for ALL catalog pages")
    parser.add_argument("--limit", type=int, default=None,
                        help="archive only the first N releases (testing)")
    args = parser.parse_args(argv)

    cfg = load_config()
    cache_dir = cfg.paths.raw / "esmis_api"
    if args.refresh_catalog and cache_dir.exists():
        for f in cache_dir.glob("*.json"):
            f.unlink()

    client = EsmisClient(cfg.esmis.base_url, cache_dir, cfg.esmis.identifier)
    releases = client.all_releases()
    print(f"catalog: {len(releases)} releases "
          f"({min(r.release_id for r in releases)} -> {max(r.release_id for r in releases)})")
    if args.limit:
        releases = releases[:args.limit]

    con = db.connect(cfg.paths.db)
    rel_rows, file_rows, downloaded, skipped, errors = [], [], 0, 0, []
    for i, rel in enumerate(releases, 1):
        try:
            archived = archive_release(rel, cfg.paths.releases,
                                       sleep_seconds=cfg.esmis.sleep_seconds)
        except Exception as exc:  # noqa: BLE001 - record and continue, rerun resumes
            errors.append((rel.release_id, str(exc)))
            print(f"  ERROR {rel.release_id}: {exc}", file=sys.stderr)
            continue
        downloaded += sum(1 for a in archived if not a.skipped)
        skipped += sum(1 for a in archived if a.skipped)
        rel_rows.append(dict(
            release_id=rel.release_id, esmis_id=rel.esmis_id, title=rel.title,
            release_datetime=rel.release_datetime, report_month=rel.report_month,
            version=rel.version, is_latest=True, format_era=rel.format_era))
        file_rows.extend(dict(
            release_id=a.release_id, ext=a.ext, url=a.url,
            local_path=str(a.local_path), sha256=a.sha256, bytes=a.bytes)
            for a in archived if a.canonical)
        if i % 25 == 0:
            print(f"  {i}/{len(releases)} releases archived "
                  f"(downloaded={downloaded} skipped={skipped})")

    rel_df = pd.DataFrame(rel_rows)
    latest = latest_release_ids([r for r in releases
                                 if r.release_id in set(rel_df["release_id"])])
    rel_df["is_latest"] = rel_df["release_id"].isin(latest)
    db.upsert(con, "releases", rel_df, ["release_id"])
    db.upsert(con, "release_files", pd.DataFrame(file_rows), ["release_id", "ext"])

    n_rel = con.execute("SELECT count(*) FROM releases").fetchone()[0]
    n_files = con.execute("SELECT count(*) FROM release_files").fetchone()[0]
    span = con.execute(
        "SELECT min(report_month), max(report_month) FROM releases").fetchone()
    print(f"done: releases={n_rel} files={n_files} span={span[0]} -> {span[1]} "
          f"downloaded={downloaded} skipped={skipped} errors={len(errors)}")
    if errors:
        for rid, msg in errors:
            print(f"  failed: {rid}: {msg}", file=sys.stderr)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
