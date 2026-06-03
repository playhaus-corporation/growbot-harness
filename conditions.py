"""
conditions.py · admissibility conditions C1–C3 (applied layer)
==============================================================

Scope honesty: C2 (quantitative conservation) and C3 (bounded scope) are fully
DETERMINISTIC — no model, so nothing to hallucinate, and anyone can re-run them.
C1 (source grounding) is deterministic for the numeric case and delegates the
irreducibly-semantic entailment to a pluggable Judge (LLM in production).

These are an applied reading of the SC-AS worked example, NOT canonical SC-AS
terms. Names are deliberately domain-specific. Read SC-CORE before using
canonical vocabulary.

Each check returns a Finding: {"result": ..., "reason": str, "score"?: float}
  PASS            condition satisfied
  FAIL            condition violated (blocks admissibility)
  NOT_APPLICABLE  condition doesn't bind this claim (e.g. C2 on a non-numeric claim)
  NEEDS_REVIEW    can't decide without a judgment that isn't available
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Quantity parsing (the core of C2)
# --------------------------------------------------------------------------- #
@dataclass
class Quantity:
    value: float
    kind: str          # 'lower' (>=), 'upper' (<=/up to), 'point' (=)
    direction: str | None  # 'reduction' | 'increase' | None
    raw: str


_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%|\b(\d+(?:\.\d+)?)\s*percent\b", re.I)
_LOWER_CUES = ("+", "or more", "at least", "over ", "more than", "minimum", "north of", "upwards of")
_UPPER_CUES = ("up to", "as much as", "as high as", "as low as", "as little as")
_REDUCE = ("reduc", "lower", "cut", "fell", "fall", "decreas", "drop", "save", "savings", "less")
_INCREASE = ("increas", "grow", "boost", "gain", "rais", "lift", "more ", "higher")


def _direction(text: str) -> str | None:
    t = text.lower()
    if any(w in t for w in _REDUCE):
        return "reduction"
    if any(w in t for w in _INCREASE):
        return "increase"
    return None


def parse_quantities(text: str) -> list[Quantity]:
    """Extract percentage quantities with a relation (lower/upper/point) and direction."""
    out: list[Quantity] = []
    t = text.lower()
    for m in _PCT.finditer(text):
        value = float(m.group(1) or m.group(2))
        start = m.start()
        window = t[max(0, start - 25): m.end() + 5]   # local context around the number
        after = t[m.end(): m.end() + 4]
        if any(cue in window for cue in _UPPER_CUES):
            kind = "upper"
        elif "+" in after or any(cue in window for cue in _LOWER_CUES):
            kind = "lower"
        else:
            kind = "point"
        out.append(Quantity(value, kind, _direction(window) or _direction(text), m.group(0)))
    return out


# --------------------------------------------------------------------------- #
# C2 — conservation under transformation (deterministic, the demo's spine)
# --------------------------------------------------------------------------- #
def check_c2_conservation(claim_text: str, source_text: str, *, tol: float = 0.08) -> dict:
    cq = parse_quantities(claim_text)
    sq = parse_quantities(source_text)
    if not cq:
        return {"result": "NOT_APPLICABLE", "reason": "no quantitative claim to conserve"}
    if not sq:
        return {"result": "FAIL", "reason": "claim is quantitative but the source carries no figure"}

    claim_q = max(cq, key=lambda q: q.value)        # the headline number in the claim
    src = max(sq, key=lambda q: q.value)            # strongest figure the source supports
    S = src.value

    # Direction mismatch (claim says 'increase', source says 'reduction') is replacement.
    if claim_q.direction and src.direction and claim_q.direction != src.direction:
        return {"result": "FAIL", "score": 0.0,
                "reason": f"direction replaced: claim '{claim_q.direction}' vs source '{src.direction}'"}

    if claim_q.kind == "lower":   # "30%+": advertises a floor the source must meet
        ok = S >= claim_q.value * (1 - tol)
        return {"result": "PASS" if ok else "FAIL",
                "score": round(min(1.0, S / claim_q.value), 3) if claim_q.value else 0.0,
                "reason": (f"source {S}% meets advertised floor {claim_q.value}%+" if ok
                           else f"source {S}% does not reach advertised floor {claim_q.value}%+ (inflation)")}
    if claim_q.kind == "upper":   # "up to 50%": ceiling can't exceed the observed result
        ok = claim_q.value <= S * (1 + tol)
        return {"result": "PASS" if ok else "FAIL",
                "score": round(min(1.0, S / claim_q.value), 3) if claim_q.value else 0.0,
                "reason": (f"advertised ceiling {claim_q.value}% within observed {S}%" if ok
                           else f"advertised ceiling {claim_q.value}% exceeds observed {S}% — replacement, not conservation")}
    # point estimate: must be close to the source value
    ok = abs(claim_q.value - S) <= tol * max(S, 1.0)
    return {"result": "PASS" if ok else "FAIL",
            "score": round(1 - abs(claim_q.value - S) / max(S, 1.0), 3),
            "reason": (f"claim {claim_q.value}% matches source {S}%" if ok
                       else f"claim {claim_q.value}% does not match source {S}%")}


# --------------------------------------------------------------------------- #
# C3 — bounded scope (deterministic lexicon of unbounded/absolute language)
# --------------------------------------------------------------------------- #
_UNBOUNDED = ("guaranteed", "guarantee", "#1", "number one", "best", "unlimited",
              "risk-free", "no risk", "always", "never fails", "fastest", "cheapest",
              "every client", "all clients", "100% of")


def check_c3_bounded_scope(claim_text: str, source_text: str = "") -> dict:
    t = claim_text.lower()
    s = source_text.lower()
    hits = [w for w in _UNBOUNDED if w in t and w not in s]   # sourced absolutes are allowed
    if hits:
        return {"result": "FAIL", "reason": f"unbounded/absolute language not supported by source: {hits}"}
    return {"result": "PASS", "reason": "no unbounded claims beyond source"}


# --------------------------------------------------------------------------- #
# C1 — source grounding (numeric is deterministic; semantic delegates to a judge)
# --------------------------------------------------------------------------- #
def check_c1_source_grounding(claim_text: str, source_span: str, judge=None) -> dict:
    if not source_span or not source_span.strip():
        return {"result": "FAIL", "reason": "no source span cited"}
    if parse_quantities(claim_text):
        if parse_quantities(source_span):
            return {"result": "PASS", "reason": "cited span carries a supporting figure (magnitude checked by C2)"}
        return {"result": "FAIL", "reason": "numeric claim but cited span has no figure"}
    if judge is None:
        return {"result": "NEEDS_REVIEW", "reason": "qualitative claim; no judge available to assess entailment"}
    ok, score, why = judge.entails(source_span, claim_text)
    return {"result": "PASS" if ok else "FAIL", "score": round(score, 3), "reason": why}
