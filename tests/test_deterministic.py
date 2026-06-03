"""
tests.test_deterministic · the offline acceptance suite
=======================================================

Drives the deterministic core through the offline LexicalJudge. No network, no keys,
no chain. These must pass before anything touches the chain. Run from growbot-harness:

    python -m pytest tests/test_deterministic.py -q
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS))

from verify.config import Config                                  # noqa: E402
from verify.deterministic import (                                # noqa: E402
    AVERAGE,
    POINT,
    ClaimRecord,
    Cmp,
    Polarity,
    SourceRecord,
    Verdict,
    check_claim,
    gate,
    more_aggressive,
)
from verify.extract import LexicalJudge                           # noqa: E402
from verify.run import verify_claim                               # noqa: E402
from verify.samples import SAMPLES                                # noqa: E402

CONFIG = Config.load()
JUDGE = LexicalJudge(CONFIG)


# --------------------------------------------------------------------------- #
# §8 / acceptance table — end-to-end through the offline judge
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sample_id", ["s1", "s2", "s3", "s4", "s5"])
def test_sample_table(sample_id):
    claim_text, source_text, expected, _rule = SAMPLES[sample_id]
    result = verify_claim(claim_text, source_text, JUDGE, config=CONFIG)
    assert result["result"] == expected, f"{sample_id}: {result['reason']}"


def test_s3_reason_is_arithmetic():
    """The demo block: the refusal must be the arithmetic, not an opinion."""
    claim_text, source_text, expected, _ = SAMPLES["s3"]
    result = verify_claim(claim_text, source_text, JUDGE, config=CONFIG)
    assert result["result"] == "INADMISSIBLE"
    assert result["normalized_comparison"] == "50 > 30 (HIGHER_IS_BETTER) -> overstatement"
    assert result["rule_id"] == "B"


def test_s6_scope_mismatch_abstains():
    """clients != customers (strict identity) → the checker abstains, never certifies."""
    claim_text, source_text, expected, _ = SAMPLES["s6"]
    result = verify_claim(claim_text, source_text, JUDGE, config=CONFIG)
    assert result["result"] == "NEEDS_REVIEW"
    assert "scope mismatch" in result["reason"]


# --------------------------------------------------------------------------- #
# Extraction robustness — a stray cue word must not bind to a distant figure.
# A deterministic mis-extraction is the one error the agreement gate can't catch
# (the same wrong read agrees with itself 3×), so the read must be figure-bound.
# --------------------------------------------------------------------------- #
def test_stray_average_word_does_not_force_average_kind():
    # "average" describes the support rating, not the $420 figure → POINT, not AVERAGE.
    _claim, source = JUDGE.extract(
        "Saves $5,000/yr.",
        "Our support is rated above average; one client saved $420/month.",
    )
    assert source.kind == POINT
    assert source.value == 420.0


def test_stray_upper_bound_cue_does_not_force_upper_bound():
    # "up to the task" is idiom, not a ceiling on the 30% figure → POINT, not UPPER_BOUND.
    claim, _source = JUDGE.extract(
        "We're up to the task and cut CAC 30%.",
        "We reduced CAC by 30%.",
    )
    assert claim.comparator is Cmp.POINT


# --------------------------------------------------------------------------- #
# Cost-metric polarity — LOWER_IS_BETTER overstatement (the inversion trap)
# --------------------------------------------------------------------------- #
def test_cost_metric_polarity_overstatement():
    # "up to $2/lead" against a $3/lead source is an OVERSTATEMENT, not understatement.
    claim = ClaimRecord("cost_per_lead_usd", Polarity.LOWER_IS_BETTER, 2.0,
                        Cmp.UPPER_BOUND, "$/lead", "up to $2/lead")
    source = SourceRecord("cost_per_lead_usd", 3.0, POINT, "$/lead", "$3 per lead")
    verdict, reason = check_claim(claim, source, CONFIG.rounding_tol("cost_per_lead_usd", 2.0))
    assert verdict is Verdict.INADMISSIBLE, reason


def test_cost_metric_understatement_admissible():
    # "up to $4/lead" against a $3 source under-claims the cheapness → admissible.
    claim = ClaimRecord("cost_per_lead_usd", Polarity.LOWER_IS_BETTER, 4.0,
                        Cmp.UPPER_BOUND, "$/lead", "up to $4/lead")
    source = SourceRecord("cost_per_lead_usd", 3.0, POINT, "$/lead", "$3 per lead")
    verdict, _ = check_claim(claim, source, CONFIG.rounding_tol("cost_per_lead_usd", 4.0))
    assert verdict is Verdict.ADMISSIBLE


# --------------------------------------------------------------------------- #
# Asymmetric rounding boundary (invariant #3)
# --------------------------------------------------------------------------- #
def test_asymmetric_rounding_boundary():
    tol = CONFIG.rounding_tol("cac_reduction_pct", 30.0)   # = 0.5
    assert tol == 0.5

    def point(v):
        return ClaimRecord("cac_reduction_pct", Polarity.HIGHER_IS_BETTER, v,
                           Cmp.POINT, "%", f"{v}%")

    src = SourceRecord("cac_reduction_pct", 30.0, POINT, "%", "30%")

    # claim 30.5 vs source 30: exactly at the rounding edge → still admissible.
    assert check_claim(point(30.5), src, tol)[0] is Verdict.ADMISSIBLE
    # claim 30.6 vs source 30: past half the LSD in the aggressive direction → fail.
    assert check_claim(point(30.6), src, tol)[0] is Verdict.INADMISSIBLE
    # understatement is always fine, regardless of distance.
    assert check_claim(point(10.0), src, tol)[0] is Verdict.ADMISSIBLE


def test_finer_claim_precision_earns_smaller_tolerance():
    # A claim stated to a tenth ("30.5%") only earns a 0.05 cushion, not 0.5.
    assert CONFIG.rounding_tol("cac_reduction_pct", 30.5) == pytest.approx(0.05)


# --------------------------------------------------------------------------- #
# Rule B — an AVERAGE never licenses a ceiling above itself
# --------------------------------------------------------------------------- #
def test_rule_b_average_above_is_inadmissible_not_review():
    claim = ClaimRecord("cac_reduction_pct", Polarity.HIGHER_IS_BETTER, 50.0,
                        Cmp.UPPER_BOUND, "%", "up to 50%")
    source = SourceRecord("cac_reduction_pct", 30.0, AVERAGE, "%", "average 30%")
    verdict, reason = check_claim(claim, source, CONFIG.rounding_tol("cac_reduction_pct", 50.0))
    assert verdict is Verdict.INADMISSIBLE
    assert "AVERAGE" in reason


# --------------------------------------------------------------------------- #
# more_aggressive primitive — polarity is not optional
# --------------------------------------------------------------------------- #
def test_more_aggressive_polarity():
    assert more_aggressive(50, 30, Polarity.HIGHER_IS_BETTER) is True
    assert more_aggressive(50, 30, Polarity.LOWER_IS_BETTER) is False
    assert more_aggressive(2, 3, Polarity.LOWER_IS_BETTER) is True


# --------------------------------------------------------------------------- #
# Extraction-agreement gate
# --------------------------------------------------------------------------- #
def test_gate_disagreement_abstains():
    c1 = ClaimRecord("cac_reduction_pct", Polarity.HIGHER_IS_BETTER, 30.0, Cmp.POINT, "%", "a")
    c2 = ClaimRecord("cac_reduction_pct", Polarity.HIGHER_IS_BETTER, 40.0, Cmp.POINT, "%", "a")
    s = SourceRecord("cac_reduction_pct", 30.0, POINT, "%", "30%")
    verdict, reason = gate([(c1, s), (c1, s), (c2, s)], 0.5)
    assert verdict is Verdict.NEEDS_REVIEW
    assert "disagreed" in reason


# --------------------------------------------------------------------------- #
# Determinism — same inputs → identical verdict + reason across 100 calls
# --------------------------------------------------------------------------- #
def test_determinism_100_calls():
    claim_text, source_text, _, _ = SAMPLES["s3"]
    first = verify_claim(claim_text, source_text, JUDGE, config=CONFIG)
    for _ in range(100):
        again = verify_claim(claim_text, source_text, JUDGE, config=CONFIG)
        assert again["result"] == first["result"]
        assert again["reason"] == first["reason"]
        assert again["normalized_comparison"] == first["normalized_comparison"]


# --------------------------------------------------------------------------- #
# Integration — cli.py --offline --dry-run --sample s3 refuses the mint
# --------------------------------------------------------------------------- #
def test_cli_offline_dry_run_s3_refuses_mint():
    proc = subprocess.run(
        [sys.executable, "cli.py", "--offline", "--dry-run", "--sample", "s3"],
        cwd=HARNESS, capture_output=True, text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    out = proc.stdout
    assert "INADMISSIBLE" in out
    assert "50 > 30" in out          # the arithmetic refusal is on screen
