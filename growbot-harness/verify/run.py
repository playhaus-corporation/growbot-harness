"""
verify.run · compose the single verification flow into one per-claim result
===========================================================================

    extract (N=3)  ->  agreement gate  ->  normalize (Rule C)  ->  check_claim (A/B)

Returns a dict carrying both the verdict and the human-legible cert fields (§5 of
the handoff / §6 of the rule table), so cli.py and the certificate builder consume
one shape. No chain access, no mint here — this is the decision, not the action.
"""

from __future__ import annotations

import math

from .config import Config
from .deterministic import (
    ClaimRecord,
    Cmp,
    SourceRecord,
    Verdict,
    agreement_key,
    check_claim,
    fnum,
)
from .normalize import normalize


def _agreement_str(extractions: list[tuple[ClaimRecord, SourceRecord]]) -> str:
    keys = {agreement_key(c) for c, _ in extractions}
    if len(keys) != 1:
        return f"split/{len(extractions)}"
    return f"{len(extractions)}/{len(extractions)}"


def _normalized_comparison(claim: ClaimRecord, source: SourceRecord, verdict: Verdict) -> str:
    # The aggressive operator (true only when the claim overstates): > for higher-is-
    # better, < for lower-is-better. For an admissible claim that operator is false, so
    # we show the within-relation instead — the string never reads as a false inequality.
    aggressive_op = ">" if claim.polarity.name == "HIGHER_IS_BETTER" else "<"
    src = source.value[1] if isinstance(source.value, (list, tuple)) else source.value
    polarity = claim.polarity.name
    if verdict is Verdict.INADMISSIBLE:
        return f"{fnum(claim.value)} {aggressive_op} {fnum(src)} ({polarity}) -> overstatement"
    if verdict is Verdict.ADMISSIBLE:
        return f"{fnum(claim.value)} within source {fnum(src)} ({polarity}) -> supported"
    return f"{fnum(claim.value)} vs {fnum(src)} ({polarity}) -> abstain"


def verify_claim(
    claim_text: str,
    source_text: str,
    judge,
    *,
    config: Config | None = None,
    n: int = 3,
) -> dict:
    """
    Run one claim end-to-end and return its verdict plus cert fields.

    `judge` is any object exposing extract_n(claim_text, source_text, n) -> list of
    (ClaimRecord, SourceRecord). The LLM (if used) is confined to that call.
    """
    config = config or Config.load()
    extractions = judge.extract_n(claim_text, source_text, n)
    claim0, source0 = extractions[0]
    tol = config.rounding_tol(claim0.metric, claim0.value) if config.known_metric(claim0.metric) else 0.0

    base = {
        "metric": claim0.metric,
        "polarity": claim0.polarity.name,
        "claim_value": None if _is_nan(claim0.value) else claim0.value,
        "claim_comparator": claim0.comparator.name,
        "unit": claim0.unit,
        "extraction_agreement": _agreement_str(extractions),
        "source_value": _jsonable(source0.value),
        "source_kind": source0.kind,
        "source_span": source0.source_span,
        "rule_id": _rule_id(claim0.comparator),
        "ruleVersion": config.rule_version,
    }

    # 1. Agreement gate — disagreement across runs is real ambiguity → abstain.
    if _agreement_str(extractions).startswith("split"):
        return {**base, "result": Verdict.NEEDS_REVIEW.value,
                "reason": "extraction disagreed across runs",
                "normalized_comparison": "n/a (extraction disagreed)"}

    # 2. Rule C — normalize onto a shared canonical unit, or abstain.
    norm = normalize(claim0, source0, config)
    if isinstance(norm[0], Verdict):
        verdict, reason = norm
        return {**base, "result": verdict.value, "reason": reason,
                "normalized_comparison": _normalized_comparison(claim0, source0, verdict)}
    claim, source = norm

    # 3. Rules A / B — the deterministic verdict over the normalized numbers.
    verdict, reason = check_claim(claim, source, tol)
    return {
        **base,
        "source_value": _jsonable(source.value),   # reflect the normalized value
        "unit": claim.unit,
        "result": verdict.value,
        "reason": reason,
        "normalized_comparison": _normalized_comparison(claim, source, verdict),
    }


def _rule_id(comparator: Cmp) -> str:
    return {"POINT": "A", "UPPER_BOUND": "B", "LOWER_BOUND": "B"}.get(comparator.name, "C")


def _is_nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


def _jsonable(value):
    if isinstance(value, (list, tuple)):
        return [None if _is_nan(v) else v for v in value]
    return None if _is_nan(value) else value
