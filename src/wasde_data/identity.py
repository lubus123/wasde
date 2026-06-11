"""Balance-sheet identity algebra shared by the OCR repair and VLM reconcile.

Identities are signed sums (sum(coef * value) == 0):
  supply:  beg + production + imports - supply_total
  use:     domestic + exports - use_total           (corn-style)
           crush + exports + seed + residual - use_total  (soybeans)
  ending:  supply_total - use_total - ending_stocks
"""

from __future__ import annotations

ID_SUPPLY = {"beginning_stocks": 1, "production": 1, "imports": 1,
             "supply_total": -1}
ID_USE_CORN = {"domestic_total": 1, "exports": 1, "use_total": -1}
ID_USE_SOY = {"crush": 1, "exports": 1, "seed": 1, "residual": 1,
              "use_total": -1}
ID_END = {"supply_total": 1, "use_total": -1, "ending_stocks": -1}
TOL = 1.6


def identities_for(commodity: str) -> list[dict[str, int]]:
    use = ID_USE_SOY if commodity == "soybeans" else ID_USE_CORN
    return [ID_SUPPLY, use, ID_END]


def _plausible(derived: float, have: dict) -> bool:
    known = [abs(v) for v in have.values() if v is not None]
    return derived >= 0 and (not known or derived <= 3 * max(known))


def solve(vals: dict, identity: dict[str, int]) -> list[str] | None:
    """None -> untestable (2+ unknowns). [] -> holds (possibly after deriving
    the single unknown). [m] -> m was derived/repaired in-place. longer list ->
    the inconsistent members (quarantine)."""
    have = {m: vals.get(m) for m in identity}
    missing = [m for m, v in have.items() if v is None]
    if len(missing) >= 2:
        return None
    if len(missing) == 1:
        m = missing[0]
        derived = -sum(c * have[k] for k, c in identity.items() if k != m) \
            / identity[m]
        if _plausible(derived, have):
            vals[m] = derived
            return [m]
        return list(identity)
    residual = sum(c * have[k] for k, c in identity.items())
    if abs(residual) <= TOL:
        return []
    repairs = []
    for m, coef in identity.items():
        derived = have[m] - residual / coef
        if _plausible(derived, {k: v for k, v in have.items() if k != m}):
            repairs.append((m, derived))
    if len(repairs) == 1:
        m, derived = repairs[0]
        vals[m] = derived
        return [m]
    return list(identity)


def repair_group(vals: dict, commodity: str) -> tuple[set[str], set[str]]:
    """(corrected, quarantined) attribute sets for one column group; vals is
    mutated with derived repairs. Iterates so a repair can unlock the next
    identity."""
    corrected: set[str] = set()
    quarantined: set[str] = set()
    for _ in range(3):
        progress = False
        for identity in identities_for(commodity):
            if all(vals.get(m) is None for m in identity):
                continue
            out = solve(vals, identity)
            if out is None:
                continue
            if len(out) == 1 and out[0] not in corrected | quarantined:
                corrected.add(out[0])
                progress = True
            elif len(out) > 1:
                quarantined.update(m for m in out if vals.get(m) is not None)
        if not progress:
            break
    corrected -= quarantined
    return corrected, quarantined


def identities_pass(vals: dict, commodity: str) -> bool:
    """True when every testable identity holds without any repair."""
    for identity in identities_for(commodity):
        have = {m: vals.get(m) for m in identity}
        if any(v is None for v in have.values()):
            continue  # untestable identities don't fail the group
        if abs(sum(c * have[k] for k, c in identity.items())) > TOL:
            return False
    return True
