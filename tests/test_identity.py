import pytest

from wasde_data.identity import identities_pass, repair_group, solve

# the verified June-1985 corn column (printed values; identities all hold)
CORN_1985 = dict(beginning_stocks=3120.0, production=4175.0, imports=2.0,
                 supply_total=7297.0, domestic_total=4709.0, exports=1865.0,
                 use_total=6574.0, ending_stocks=723.0)


def test_identities_pass_on_clean_column():
    assert identities_pass(dict(CORN_1985), "corn")


def test_identities_fail_on_single_bad_cell():
    vals = dict(CORN_1985, beginning_stocks=4120.0)  # the actual tesseract misread
    assert not identities_pass(vals, "corn")


def test_solve_derives_single_missing_member():
    vals = dict(CORN_1985)
    del vals["imports"]
    from wasde_data.identity import ID_SUPPLY
    out = solve(vals, ID_SUPPLY)
    assert out == ["imports"]
    assert vals["imports"] == pytest.approx(2.0)


def test_single_identity_cannot_localize_its_own_bad_member():
    """Any member of one identity can absorb the residual -> ambiguous.
    This is WHY the second OCR reader exists."""
    from wasde_data.identity import ID_SUPPLY
    vals = dict(CORN_1985, beginning_stocks=4120.0)
    out = solve(vals, ID_SUPPLY)
    assert out is not None and len(out) > 1  # quarantine, not a guess


def test_localize_succeeds_for_doubly_constrained_cell():
    """supply_total appears in two identities: corrupting it breaks both, and
    only its repair fixes both -> uniquely localizable without a second reader."""
    from wasde_data.identity import localize_single_bad
    vals = dict(CORN_1985, supply_total=7397.0)
    hit = localize_single_bad(vals, "corn")
    assert hit is not None
    member, derived = hit
    assert member == "supply_total" and derived == pytest.approx(7297.0)


def test_localize_with_reader_disagreement_candidates():
    """When the readers agree on every cell except one, the candidate set
    collapses to that cell and the identity system adjudicates it."""
    from wasde_data.identity import localize_single_bad
    vals = dict(CORN_1985, beginning_stocks=4120.0)  # tesseract's misread
    hit = localize_single_bad(vals, "corn", candidates={"beginning_stocks"})
    assert hit is not None
    member, derived = hit
    assert member == "beginning_stocks" and derived == pytest.approx(3120.0)


def test_solve_returns_group_when_ambiguous():
    from wasde_data.identity import ID_END
    # ending = supply - use; corrupt two members -> no unique repair
    vals = dict(supply_total=7297.0, use_total=6000.0, ending_stocks=100.0)
    out = solve(vals, ID_END)
    assert out is not None and len(out) > 1


def test_solve_untestable_with_two_unknowns():
    from wasde_data.identity import ID_SUPPLY
    vals = dict(beginning_stocks=None, production=4175.0, imports=None,
                supply_total=7297.0)
    assert solve(vals, ID_SUPPLY) is None


def test_repair_group_full_clean_column():
    corrected, quarantined = repair_group(dict(CORN_1985), "corn")
    assert corrected == set() and quarantined == set()


def test_repair_group_chains_repairs():
    # missing imports AND ending stocks: supply identity derives imports,
    # then the ending identity derives ending stocks
    vals = dict(CORN_1985)
    del vals["imports"], vals["ending_stocks"]
    corrected, quarantined = repair_group(vals, "corn")
    assert corrected == {"imports", "ending_stocks"}
    assert quarantined == set()
    assert vals["ending_stocks"] == pytest.approx(723.0)


def test_repair_group_quarantines_unrepairable():
    vals = dict(CORN_1985, beginning_stocks=4120.0, production=5175.0)  # 2 bad
    corrected, quarantined = repair_group(vals, "corn")
    assert "beginning_stocks" in quarantined or "production" in quarantined
    assert not corrected & quarantined


def test_soybean_use_identity():
    soy = dict(beginning_stocks=340.0, production=4435.0, imports=25.0,
               supply_total=4800.0, crush=2750.0, exports=1630.0, seed=72.0,
               residual=38.0, use_total=4490.0, ending_stocks=310.0)
    assert identities_pass(soy, "soybeans")
    bad = dict(soy, crush=2850.0)
    assert not identities_pass(bad, "soybeans")
    # crush appears in one identity only -> not localizable without reader #2
    from wasde_data.identity import localize_single_bad
    assert localize_single_bad(bad, "soybeans") is None
    # ...but with the disputed-cell candidate set, it is
    hit = localize_single_bad(bad, "soybeans", candidates={"crush"})
    assert hit == ("crush", pytest.approx(2750.0))
