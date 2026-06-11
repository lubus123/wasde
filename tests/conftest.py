from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def golden_dir() -> Path:
    return GOLDEN


@pytest.fixture
def tmp_db(tmp_path):
    from wasde_data.db import connect
    con = connect(tmp_path / "test.duckdb")
    yield con
    con.close()


@pytest.fixture
def registry():
    from wasde_data.registry import Registry
    return Registry()
