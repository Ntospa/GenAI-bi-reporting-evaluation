"""Validation layer (core of RQ1/RQ2).

Extracts numeric claims from generated text and checks each against the canonical KPIs.
Produces numeric_claim_faithfulness, numeric_hallucination_rate, KPI coverage, and
cross-run consistency. All metrics cover numeric claims only (extracted numbers vs kpis.json).
"""
from __future__ import annotations
import re
from itertools import combinations

# A numeric token: a run of digits possibly containing '.', ',' or spaces as separators.
# Optional trailing unit. locale-robust: handles both en-US (1,234.56) and European (1.234,56).
_NUM = re.compile(r"([+-]?\d[\d.,  ]*\d|\d)\s*(%|€|eur|EUR)?", re.I)
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")


def _candidate_values(raw: str):
    """Return the set of plausible float interpretations of a numeric token, covering both
    en-US and European conventions (we match if any interpretation matches a KPI)."""
    a = raw.replace(" ", "").replace(" ", "")
    cands = set()
    for norm in (
        a.replace(",", ""),                 # en-US: comma=thousands, dot=decimal
        a.replace(".", "").replace(",", "."),  # European: dot=thousands, comma=decimal
        re.sub(r"[.,]", "", a),             # all separators removed (pure integer/thousands)
    ):
        try:
            cands.add(float(norm))
        except ValueError:
            pass
    return cands


def _iter_claims(text: str):
    """Yield (cands:set[float], unit:str|None, raw:str, context:str) per numeric claim.
    Single source of truth for extraction; extract_claims() and the spot-check both use it.

    Date tokens (YYYY-MM[-DD]) are stripped first; standalone years and tiny bare integers are
    ignored (references, not KPI claims). Locale-robust: see _candidate_values.
    """
    text = _DATE.sub(" ", text)
    for m in _NUM.finditer(text):
        raw, unit = m.group(1), m.group(2)
        cands = _candidate_values(raw)
        if not cands:
            continue
        unit_norm = "EUR" if (unit and unit.lower() in ("eur", "€")) else ("%" if unit == "%" else None)
        has_sep = ("," in raw) or ("." in raw) or (" " in raw) or (" " in raw)
        # ignore bare integers with no separator/unit (product codes, list indices, years)
        if unit_norm is None and not has_sep:
            v = next(iter(cands))
            if v.is_integer() and (abs(v) <= 3 or 1900 <= v <= 2100):
                continue
        # a number glued to a letter (e.g. a code) - skip when no unit
        end = m.end(1)
        if unit_norm is None and end < len(text) and text[end:end + 1].isalpha():
            continue
        context = text[max(0, m.start(1) - 35):end + 12].replace("\n", " ").strip()
        yield cands, unit_norm, raw, context


def extract_claims(text: str):
    """Return list of (candidates:set[float], unit:str|None) numeric claims found in text."""
    return [(c, u) for c, u, _raw, _ctx in _iter_claims(text)]


def _kpi_values(kpis: dict):
    return [(v["value"], v["unit"].upper()) for v in kpis.values()]


def _acceptable_values(kpis: dict):
    """Values a report may legitimately state, each tagged 'raw' or 'derived'.
    Option A: besides raw KPIs, we credit verifiable derivations the task explicitly allows -
    shares (KPI ÷ total_revenue × 100) and within-family absolute differences - so that correct
    computed numbers are not counted as hallucinations."""
    acc = [(v["value"], v["unit"].upper(), "raw") for v in kpis.values()]
    # total revenue (for shares)
    total = next((v["value"] for k, v in kpis.items()
                  if k == "total_revenue" and v["unit"].upper() == "EUR"), None)
    eur = [(k, v["value"]) for k, v in kpis.items() if v["unit"].upper() == "EUR"]
    if total:
        for k, val in eur:
            if k != "total_revenue" and total:
                acc.append((round(val / total * 100, 2), "%", "derived"))  # share of total
    # within-family absolute differences (e.g. category vs category, region vs region)
    def fam(k):
        return k.split("::")[0]
    for i in range(len(eur)):
        for j in range(i + 1, len(eur)):
            if fam(eur[i][0]) == fam(eur[j][0]):
                acc.append((round(abs(eur[i][1] - eur[j][1]), 2), "EUR", "derived"))
    # category/region MoM changes: credited only when both current and previous-period values
    # for the same segment are present in the provided data (deterministic from inputs).
    cur_seg, prev_seg = {}, {}
    for k, v in kpis.items():
        if "::" not in k:
            continue
        fam0, seg = k.split("::", 1)
        base = fam0.split(" (")[0].strip()          # e.g. 'revenue_by_category'
        if "(" in fam0:                              # previous-period key: '... (2013-11)'
            prev_seg[(base, seg)] = v["value"]
        else:
            cur_seg[(base, seg)] = v["value"]
    for key in set(cur_seg) & set(prev_seg):
        c, p = cur_seg[key], prev_seg[key]
        acc.append((round(abs(c - p), 2), "EUR", "derived"))           # absolute MoM change
        if p:
            acc.append((round((c - p) / p * 100, 2), "%", "derived"))  # % MoM change per segment
    # total-level MoM change (current total_revenue vs revenue_previous_period) - the total delta
    # is deterministically derivable from the two provided totals, so credit it like segment deltas.
    total_cur = next((v["value"] for k, v in kpis.items() if k == "total_revenue"), None)
    total_prev = next((v["value"] for k, v in kpis.items()
                       if k.startswith("revenue_previous_period")), None)
    if total_cur is not None and total_prev not in (None, 0):
        acc.append((round(abs(total_cur - total_prev), 2), "EUR", "derived"))
        acc.append((round((total_cur - total_prev) / total_prev * 100, 2), "%", "derived"))
    return acc


_PCT_ABS_FLOOR = 0.5   # percentage points: accept normal rounding of shares (e.g. 10.87 -> 11)


def _matches(claim, acc_vals, rel_tol):
    """Return (matched_value, kind) or None. kind in {'raw','derived'}.

    Prefers raw over derived; within a kind, picks the nearest acceptable value (avoids
    cross-KPI collisions when two KPIs fall within tolerance). For percentages, a small
    absolute floor (0.5pp) is added to the relative tolerance so ordinary rounding of small
    shares is credited (a 1% relative tolerance is too tight for values like 1.86%)."""
    cands, cunit = claim
    best_raw = best_der = None  # (kval, distance)
    for kval, kunit, kind in acc_vals:
        if cunit and kunit and cunit != kunit:
            continue
        tol = max(abs(kval) * rel_tol, 0.01)
        if kunit == "%" or cunit == "%":
            tol = max(tol, _PCT_ABS_FLOOR)
        for cval in cands:
            d = abs(cval - kval)
            if d <= tol:
                if kind == "raw" and (best_raw is None or d < best_raw[1]):
                    best_raw = (kval, d)
                elif kind == "derived" and (best_der is None or d < best_der[1]):
                    best_der = (kval, d)
    if best_raw is not None:
        return (best_raw[0], "raw")
    if best_der is not None:
        return (best_der[0], "derived")
    return None


def _strip_product_names(text: str, kpis: dict) -> str:
    """Remove product-identifier strings (from top_products KPI keys) so embedded model numbers
    and sizes (e.g. 'Mountain-200 Silver, 38') are not mis-extracted as numeric claims."""
    for k in kpis:
        if k.startswith("top_products::"):
            name = k.split("::", 1)[1].split("_", 1)[-1].strip()  # e.g. 'Mountain-200 Silver, 38'
            if name:
                text = text.replace(name, " ")
    return text


def validate_report(text: str, kpis: dict, rel_tol: float = 0.01) -> dict:
    claims = extract_claims(_strip_product_names(text, kpis))
    acc_vals = _acceptable_values(kpis)
    sup_raw, sup_derived, unsupported, matched_values = 0, 0, 0, set()
    for c in claims:
        hit = _matches(c, acc_vals, rel_tol)
        if hit is None:
            unsupported += 1
        else:
            kval, kind = hit
            if kind == "raw":
                sup_raw += 1
                matched_values.add(round(kval, 2))   # kpi_coverage counts raw KPIs only
            else:
                sup_derived += 1
    supported = sup_raw + sup_derived
    total = supported + unsupported
    faithfulness = supported / total if total else 1.0
    hallucination_rate = unsupported / total if total else 0.0
    kpi_coverage = len(matched_values) / len(kpis) if kpis else 0.0
    # NOTE: numeric claims only. Option A: 'supported' credits raw KPI values AND verifiable
    # derivations (shares = KPI/total*100, within-family absolute differences).
    return {
        "n_numeric_claims": total,
        "n_supported": supported,
        "n_supported_raw": sup_raw,
        "n_supported_derived": sup_derived,
        "n_unsupported": unsupported,
        "numeric_claim_faithfulness": round(faithfulness, 4),
        "numeric_hallucination_rate": round(hallucination_rate, 4),
        "kpi_coverage": round(kpi_coverage, 4),
        "matched_values": sorted(matched_values),
    }


def consistency(run_reports: list[dict]) -> dict:
    """Cross-run consistency (RQ2) over the per-run validation dicts."""
    if len(run_reports) < 2:
        return {"runs": len(run_reports), "note": "need >=2 runs"}
    faiths = [r["numeric_claim_faithfulness"] for r in run_reports]
    covs = [r["kpi_coverage"] for r in run_reports]
    value_sets = [set(r["matched_values"]) for r in run_reports]
    jac = []
    for a, b in combinations(value_sets, 2):
        u = a | b
        jac.append(len(a & b) / len(u) if u else 1.0)
    mean = lambda xs: sum(xs) / len(xs)
    std = lambda xs: (sum((x - mean(xs)) ** 2 for x in xs) / len(xs)) ** 0.5
    return {
        "runs": len(run_reports),
        "numeric_claim_faithfulness_mean": round(mean(faiths), 4),
        "numeric_claim_faithfulness_std": round(std(faiths), 4),
        "coverage_mean": round(mean(covs), 4),
        "coverage_std": round(std(covs), 4),
        "value_set_jaccard_mean": round(mean(jac), 4),
    }
