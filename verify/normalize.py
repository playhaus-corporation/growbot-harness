"""
verify.normalize · Rule C — unit / period / scope normalization (the silent killer)
===================================================================================

Runs *before* Rule A or B. It converts the source onto the claim's canonical unit
and reconciles scope. If normalization is impossible or ambiguous it returns
NEEDS_REVIEW — abstaining before any arithmetic runs. The traps it must catch:

  period:   $420/month  ->  $5,040/year   (×12)
  unit:     "30%"  is NOT  "30 percentage points"  (ratio vs points — never conflate)
  scope:    "clients"  vs  "customers"  — different populations -> NEEDS_REVIEW

Output is either ("NEEDS_REVIEW", reason) or a (claim, source) pair on a shared
canonical unit, ready for deterministic.check_claim.
"""

from __future__ import annotations

from dataclasses import replace

from .config import Config
from .deterministic import ClaimRecord, SourceRecord, Verdict


def _convert_period(source: SourceRecord, target_unit: str, factor: float) -> SourceRecord:
    """Scale a source value (and its unit label) by a period factor, e.g. /month -> /year."""
    if isinstance(source.value, (list, tuple)):
        scaled = [v * factor for v in source.value]
    else:
        scaled = source.value * factor
    return replace(source, value=scaled, unit=target_unit)


def normalize(
    claim: ClaimRecord, source: SourceRecord, config: Config
) -> tuple[ClaimRecord, SourceRecord] | tuple[Verdict, str]:
    """
    Reconcile claim and source onto one canonical unit, or abstain.

    Returns (claim, source) when they can be compared, or (Verdict.NEEDS_REVIEW, reason)
    when they cannot. Callers must check the type of the first element.
    """
    # An unknown metric has no polarity/unit/precision to verify against — abstain.
    if not config.known_metric(claim.metric):
        return Verdict.NEEDS_REVIEW, f"unknown metric '{claim.metric}'; no rule to apply"

    # Different metric keys = potentially different populations. This is the scope
    # reconciliation decision — see populations_match below.
    if claim.metric != source.metric:
        if not populations_match(claim.metric, source.metric, config):
            return Verdict.NEEDS_REVIEW, (
                f"scope mismatch: claim metric '{claim.metric}' vs source metric "
                f"'{source.metric}' — different populations, refusing to guess"
            )
        # Same population under two names: rebind the source onto the claim's metric.
        source = replace(source, metric=claim.metric, unit=config.unit(claim.metric))

    canonical_unit = config.unit(claim.metric)

    # The claim must already be on the canonical unit; an off-unit claim (e.g. ratio
    # vs percentage-points) is exactly the conflation Rule C exists to refuse.
    if claim.unit != canonical_unit:
        return Verdict.NEEDS_REVIEW, (
            f"claim unit '{claim.unit}' is not the canonical '{canonical_unit}' "
            f"for {claim.metric} (do not conflate distinct units)"
        )

    # Bring the source onto the canonical unit: identity, a known period conversion,
    # or otherwise irreconcilable.
    if source.unit != canonical_unit:
        factor = config.period_conversions(claim.metric).get(source.unit)
        if factor is None:
            return Verdict.NEEDS_REVIEW, (
                f"source unit '{source.unit}' cannot be normalized to "
                f"'{canonical_unit}' for {claim.metric}"
            )
        source = _convert_period(source, canonical_unit, factor)

    return claim, source


# --------------------------------------------------------------------------- #
# CONTRIBUTION POINT — scope reconciliation policy (invariant #2)
# --------------------------------------------------------------------------- #
def populations_match(claim_metric: str, source_metric: str, config: Config) -> bool:
    """
    Decide whether two *different* metric keys name the SAME underlying population.

    This is only called when claim.metric != source.metric, and it is the single
    most consequential judgment in Rule C. Return True only when you can defend that
    the two labels denote the same thing; return False to abstain (-> NEEDS_REVIEW).

    The sample that exercises this: claim "1,000+ clients" (clients_count) vs source
    "over 1,000 customers" (customers_count). "clients" and "customers" are different
    populations — an agency has far more customers than retained clients — so the
    honest verdict is NEEDS_REVIEW, not a silent pass. Refusing to guess here is the
    rigor the whole design exists to protect.

    Trade-offs to weigh in your implementation (~5-10 lines):
      - Strict identity (always False unless metrics are literally equal) is the most
        conservative and never wrong-but-confident, but routes more claims to humans.
      - A curated synonym set in rules.json (e.g. an explicit "sameAs" list per metric)
        lets you reconcile genuine synonyms while still abstaining on everything else —
        more permissive, but every entry is a human-audited assertion, not a guess.
      - Fuzzy string similarity (clients ~ customers) is tempting and *wrong* here:
        it is exactly the kind of plausible-but-unfounded reconciliation that puts a
        cryptographic seal on a bad claim. Don't.

    Policy: STRICT IDENTITY. This function is only reached when the metric keys
    already differ, so strict identity reconciles nothing — two differently-named
    metrics are treated as different populations, and the claim routes to NEEDS_REVIEW.
    It is the most conservative policy: it never reconciles by guessing, which is
    exactly the failure mode (a cryptographic seal on an unfounded equivalence) the
    design exists to prevent. To loosen it later, replace the body with a curated
    `sameAs` allowlist read from `config` — never fuzzy string similarity.
    """
    return claim_metric == source_metric
