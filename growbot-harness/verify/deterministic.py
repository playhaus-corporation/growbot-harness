"""
verify.deterministic · the pure verification core
=================================================

Zero external dependencies. No API keys. No chain access. No clock, RNG, network,
or env reads inside check_claim / gate. This is what makes an anchored verdict
reproducible by anyone holding only the Python standard library.

The LLM never reaches this module. It produces ClaimRecord / SourceRecord upstream
(extract.py); everything here is arithmetic over those numbers.

Rules:
  A  Magnitude match — POINT claim vs POINT/AVERAGE source.
  B  Bound vs evidence — UPPER_BOUND "up to X" needs the source to *reach* X.
  C  Unit/scope normalization — runs in normalize.py *before* this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Polarity(Enum):
    HIGHER_IS_BETTER = "higher"   # a bigger number is the stronger claim (e.g. % reduction)
    LOWER_IS_BETTER = "lower"     # a smaller number is the stronger claim (e.g. $/lead)


class Cmp(Enum):
    POINT = "point"               # "X"        — X is representative
    UPPER_BOUND = "upper"         # "up to X"  — X is reachable (the impressive ceiling)
    LOWER_BOUND = "lower"         # "at least X" / "over X" — X is a floor for all/typical


class Verdict(Enum):
    ADMISSIBLE = "ADMISSIBLE"     # source deterministically licenses the claim
    INADMISSIBLE = "INADMISSIBLE"  # claim is more aggressive than the source supports
    NEEDS_REVIEW = "NEEDS_REVIEW"  # the checker abstains; routed to a human, never certified


# Source kinds. POINT/AVERAGE/MAX_OBSERVED/RANGE document a value; only POINT and
# MIN_OBSERVED establish a *floor* a LOWER_BOUND claim can stand on.
POINT = "POINT"
RANGE = "RANGE"
MAX_OBSERVED = "MAX_OBSERVED"
MIN_OBSERVED = "MIN_OBSERVED"
AVERAGE = "AVERAGE"
# A figure stated as a GOAL, not an observed result ("we aim as high as 60%"). It
# documents nothing, so it licenses no claim — check_claim abstains on it. Set by the
# judge-independent guard extract.demote_if_aspirational (applied in run.verify_claim).
ASPIRATIONAL = "ASPIRATIONAL"


@dataclass(frozen=True)
class ClaimRecord:
    metric: str
    polarity: Polarity
    value: float
    comparator: Cmp
    unit: str
    claim_span: str


@dataclass(frozen=True)
class SourceRecord:
    metric: str
    value: float | list[float] | tuple[float, float]  # scalar, or [lo, hi] for a RANGE
    kind: str                                          # one of the *_OBSERVED / POINT / RANGE / AVERAGE
    unit: str
    source_span: str


def fnum(x) -> str:
    """Render a number for human-legible reasons: 50.0 -> '50', 30.5 -> '30.5'."""
    if isinstance(x, (list, tuple)):
        return "[" + ", ".join(fnum(v) for v in x) + "]"
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def more_aggressive(a: float, b: float, p: Polarity) -> bool:
    """True when value `a` is a *stronger* claim than `b` under polarity `p`."""
    return a > b if p is Polarity.HIGHER_IS_BETTER else a < b


def _aggressive_edge(value, p: Polarity) -> float:
    """
    Collapse a source value to the single number a claim is tested against.

    For a RANGE the relevant edge is the most aggressive one: a ceiling can only be
    'reached' by the strongest documented result (max if higher-is-better, min if
    lower-is-better). For a scalar this is the value itself.
    """
    if isinstance(value, (list, tuple)):
        return max(value) if p is Polarity.HIGHER_IS_BETTER else min(value)
    return value


def check_claim(c: ClaimRecord, s: SourceRecord, tol: float) -> tuple[Verdict, str]:
    """
    The deterministic verdict for one normalized (claim, source) pair.

    Rule C is assumed already applied: if units/metrics could not be reconciled the
    caller returned NEEDS_REVIEW before reaching here. `tol` is the *asymmetric*
    rounding allowance (see config.rounding_tol): a claim may round in its favor by
    up to tol, but never exceed the source beyond that in the aggressive direction.
    """
    if s.kind == ASPIRATIONAL:
        return Verdict.NEEDS_REVIEW, "source figure is a stated goal/aspiration, not observed evidence"

    if c.metric != s.metric or c.unit != s.unit:
        return Verdict.NEEDS_REVIEW, "unit/metric not reconciled"

    src = _aggressive_edge(s.value, c.polarity)
    # Tolerance is applied only in the conservative direction — it relaxes the
    # source toward the claim, never the claim past the source.
    src_with_tol = (src + tol) if c.polarity is Polarity.HIGHER_IS_BETTER else (src - tol)

    if c.comparator is Cmp.POINT:                                  # Rule A
        if more_aggressive(c.value, src_with_tol, c.polarity):
            return Verdict.INADMISSIBLE, (
                f"{fnum(c.value)} exceeds source {fnum(src)} ({c.polarity.value}) beyond rounding"
            )
        return Verdict.ADMISSIBLE, f"{fnum(c.value)} within source {fnum(src)} ({c.polarity.value})"

    if c.comparator is Cmp.UPPER_BOUND:                            # Rule B
        # An AVERAGE affirmatively fails to establish a reachable ceiling above
        # itself — so a ceiling above an average is INADMISSIBLE, never abstain.
        if more_aggressive(c.value, src_with_tol, c.polarity):
            detail = (
                f"ceiling {fnum(c.value)} above source {fnum(src)} (AVERAGE establishes no higher value)"
                if s.kind == AVERAGE
                else f"ceiling {fnum(c.value)} unreached by source {fnum(src)} ({s.kind})"
            )
            return Verdict.INADMISSIBLE, detail
        return Verdict.ADMISSIBLE, f"ceiling {fnum(c.value)} reached by source {fnum(src)} ({s.kind})"

    if c.comparator is Cmp.LOWER_BOUND:                            # floors need floor evidence
        # AVERAGE / MAX / RANGE document a value but never guarantee a floor; only an
        # explicit minimum or a single point does. Absent that, abstain.
        if s.kind not in (MIN_OBSERVED, POINT):
            return Verdict.NEEDS_REVIEW, f"no documented floor in source ({s.kind})"
        if more_aggressive(c.value, src_with_tol, c.polarity):
            return Verdict.INADMISSIBLE, f"floor {fnum(c.value)} not guaranteed by source {fnum(src)}"
        return Verdict.ADMISSIBLE, f"floor {fnum(c.value)} guaranteed by source {fnum(src)}"

    return Verdict.NEEDS_REVIEW, "unhandled comparator"


def agreement_key(c: ClaimRecord) -> tuple:
    """The tuple the 3 extraction runs must agree on. Single source of truth for
    'agreement' — both gate() and the cert's extraction_agreement field use it."""
    return (c.metric, c.polarity, c.value, c.comparator, c.unit)


def gate(extractions: list[tuple[ClaimRecord, SourceRecord]], tol: float) -> tuple[Verdict, str]:
    """
    Extraction-agreement gate. The redundancy is on the *read*, not the verdict.

    If the N (=3) extraction runs disagree on (metric, polarity, value, comparator,
    unit) the claim text is genuinely ambiguous → NEEDS_REVIEW, a human should look.
    Disagreement is real, *random* signal — unlike voting on the verdict, where
    correlated model errors would sail through 3/3. If they agree, run check_claim.
    """
    if not extractions:
        return Verdict.NEEDS_REVIEW, "no extractions"
    if len({agreement_key(c) for c, _ in extractions}) != 1:
        return Verdict.NEEDS_REVIEW, "extraction disagreed across runs"
    claim, source = extractions[0]
    return check_claim(claim, source, tol)
