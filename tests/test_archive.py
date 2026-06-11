import hashlib
import json
from datetime import datetime

from wasde_data.archive import archive_release
from wasde_data.esmis import Release


def _release() -> Release:
    return Release(
        esmis_id="795937", title="WASDE",
        release_datetime=datetime(2026, 6, 11, 12, 0),
        files=("https://x.test/files/wasde0626.xml",
               "https://x.test/files/wasde0626.pdf"),
    )


def test_archive_release_writes_files_and_manifest(tmp_path):
    fetched = []

    def fake_download(url):
        fetched.append(url)
        return b"content-of-" + url.encode()

    out = archive_release(_release(), tmp_path, sleep_seconds=0, download=fake_download)
    assert len(out) == 2 and not any(f.skipped for f in out)
    month_dir = tmp_path / "2026-06"
    xml = month_dir / "wasde-2026-06-11.xml"
    assert xml.read_bytes() == b"content-of-https://x.test/files/wasde0626.xml"
    assert (month_dir / "wasde-2026-06-11.pdf").exists()

    manifest = [json.loads(line) for line in
                (month_dir / "manifest.jsonl").read_text().splitlines()]
    assert len(manifest) == 2
    assert manifest[0]["release_id"] == "wasde-2026-06-11"
    assert manifest[0]["sha256"] == hashlib.sha256(xml.read_bytes()).hexdigest()


def test_archive_release_is_idempotent_and_never_rewrites(tmp_path):
    def first_download(url):
        return b"original"

    def poisoned_download(url):
        raise AssertionError("network touched for an already-archived file")

    archive_release(_release(), tmp_path, sleep_seconds=0, download=first_download)
    out = archive_release(_release(), tmp_path, sleep_seconds=0, download=poisoned_download)
    assert all(f.skipped for f in out)
    assert (tmp_path / "2026-06" / "wasde-2026-06-11.xml").read_bytes() == b"original"
    # manifest not duplicated by the skip pass
    lines = (tmp_path / "2026-06" / "manifest.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_archive_versioned_release_names(tmp_path):
    rel = Release(esmis_id="795903", title="WASDE",
                  release_datetime=datetime(2026, 5, 12, 12, 0),
                  files=("https://x.test/files/wasde0526v2.xml",))
    out = archive_release(rel, tmp_path, sleep_seconds=0, download=lambda u: b"x")
    assert out[0].local_path.name == "wasde-2026-05-12-v2.xml"
