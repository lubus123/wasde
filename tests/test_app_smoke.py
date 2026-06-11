"""Headless smoke tests: every Streamlit page renders against the real exports.

Requires the [app] extra (streamlit); skipped cleanly on a core+dev install.
Catches import errors, query bugs, and widget wiring — visual QA is done
separately via screenshots.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app"
# `streamlit run` puts the app dir on sys.path; AppTest does not.
sys.path.insert(0, str(APP))
EXPORTS = Path(__file__).resolve().parents[1] / "data" / "exports"

pytestmark = pytest.mark.skipif(
    not (EXPORTS / "observations.parquet").exists(),
    reason="parquet exports not built",
)

PAGES = [
    "Home.py",
    "pages/1_Vintage_Progression.py",
    "pages/2_Report_Month_Matrix.py",
    "pages/3_Bias_Explorer.py",
    "pages/4_Revision_Momentum.py",
    "pages/5_Surprise_Leaderboard.py",
    "pages/6_Coverage.py",
]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page):
    at = AppTest.from_file(str(APP / page), default_timeout=120)
    at.run()
    assert not at.exception, f"{page} raised: {[e.value for e in at.exception]}"


def _box(at: AppTest, label: str):
    return next(s for s in at.selectbox if s.label == label)


def test_matrix_page_reacts_to_month_change():
    at = AppTest.from_file(str(APP / "pages/2_Report_Month_Matrix.py"),
                           default_timeout=120)
    at.run()
    _box(at, "Report month").select("Jul").run()
    assert not at.exception


def test_vintage_page_world_dataset():
    at = AppTest.from_file(str(APP / "pages/1_Vintage_Progression.py"),
                           default_timeout=120)
    at.run()
    _box(at, "Dataset").set_value("World — Soybeans").run()
    assert not at.exception
