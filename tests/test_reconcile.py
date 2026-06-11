import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "reconcile", Path(__file__).resolve().parents[1] / "scripts" / "06_reconcile.py")
reconcile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reconcile)

# the verified June-1985 corn column
TRUE = dict(beginning_stocks=3120.0, production=4175.0, imports=2.0,
            supply_total=7297.0, domestic_total=4709.0, exports=1865.0,
            use_total=6574.0, ending_stocks=723.0)


def test_full_agreement_is_ok():
    out = reconcile.arbitrate_group(dict(TRUE), dict(TRUE), "corn")
    assert all(status == "ok" for _, status in out.values())
    assert out["ending_stocks"] == (723.0, "ok")


def test_single_disagreement_resolved_by_identities():
    """The actual 1985 case: tesseract misread beginning stocks; readers agree
    everywhere else, so the identity system picks GOT's value."""
    tess = dict(TRUE, beginning_stocks=4120.0)
    got = dict(TRUE)
    out = reconcile.arbitrate_group(tess, got, "corn")
    assert out["beginning_stocks"] == (3120.0, "corrected")
    assert out["production"] == (4175.0, "ok")


def test_two_disagreements_resolved_jointly():
    tess = dict(TRUE, beginning_stocks=4120.0, use_total=6974.0)
    got = dict(TRUE)
    out = reconcile.arbitrate_group(tess, got, "corn")
    assert out["beginning_stocks"] == (3120.0, "corrected")
    assert out["use_total"] == (6574.0, "corrected")


def test_cell_missing_from_one_reader_recovered():
    tess = dict(TRUE)
    del tess["imports"]  # tesseract couldn't read the row ('t 2 Zz 1 i')
    got = dict(TRUE)
    out = reconcile.arbitrate_group(tess, got, "corn")
    assert out["imports"] == (2.0, "corrected")


def test_both_readers_wrong_same_cell_quarantines():
    """If both misread the same cell (differently), no combination passes."""
    tess = dict(TRUE, production=4275.0)
    got = dict(TRUE, production=4975.0)
    out = reconcile.arbitrate_group(tess, got, "corn")
    assert out["production"][1] == "quarantined"
    # agreed cells stay verified
    assert out["exports"] == (1865.0, "ok")


def test_untestable_group_quarantines_disputes():
    """No totals present -> identities untestable -> disagreement can't be
    arbitrated."""
    tess = dict(farm_price=3.25)
    got = dict(farm_price=3.35)
    out = reconcile.arbitrate_group(tess, got, "corn")
    assert out["farm_price"][1] == "quarantined"


def test_single_reader_cell_outside_identities_is_warn_not_quarantine():
    """Sub-stock rows (CCC etc.) sit outside every identity; if only one
    reader captured them there is nothing to test — keep visible as 'warn'."""
    tess = dict(TRUE, ccc_inventory=201.0)
    got = dict(TRUE)  # Paddle didn't read the CCC row
    out = reconcile.arbitrate_group(tess, got, "corn")
    assert out["ccc_inventory"] == (201.0, "warn")
    assert out["ending_stocks"] == (723.0, "ok")  # agreed cells unaffected


def test_isolated_agreement_is_ok_even_without_identities():
    out = reconcile.arbitrate_group(dict(farm_price=3.25), dict(farm_price=3.25),
                                    "corn")
    assert out["farm_price"] == (3.25, "ok")


@pytest.mark.parametrize("attr", ["supply_total", "use_total"])
def test_doubly_constrained_cells_self_arbitrate(attr):
    tess = dict(TRUE)
    tess[attr] = TRUE[attr] + 100
    out = reconcile.arbitrate_group(tess, dict(TRUE), "corn")
    assert out[attr] == (TRUE[attr], "corrected")
