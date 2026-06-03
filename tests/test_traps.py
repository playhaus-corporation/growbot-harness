"""
tests.test_traps · adversarial soundness suite (the correlated-misread seam)
============================================================================

The N=3 agreement gate catches *random* extraction error (ambiguous text → runs
diverge → NEEDS_REVIEW). It is blind to a *correlated* misread: a confident, stable
wrong read that agrees with itself every time. Those have to be caught structurally,
in extraction + Rule C, or they get a cryptographic seal on a false claim.

So we drive deceptive (claim, source) text through the real offline pipeline with the
deterministic LexicalJudge — which is itself a stable, confident reader, i.e. a faithful
stand-in for "the model misread it the same way 3×". The cardinal invariant:

    a deceptive pair is NEVER ADMISSIBLE.  (INADMISSIBLE or NEEDS_REVIEW are both safe.)

Certifying a false claim is the one failure this whole design exists to prevent;
refusing or abstaining is always acceptable. Controls prove the suite isn't trivially
"refuse everything" — legitimate claims must still pass.

Run from growbot-harness:

    python -m pytest tests/test_traps.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS))

from verify.config import Config          # noqa: E402
from verify.extract import LexicalJudge   # noqa: E402
from verify.run import verify_claim       # noqa: E402

CONFIG = Config.load()
JUDGE = LexicalJudge(CONFIG)


def _verdict(claim: str, source: str) -> dict:
    return verify_claim(claim, source, JUDGE, config=CONFIG)


# (id, claim, source, expected_verdict, the lie it encodes) -------------------
# Each must resolve to its expected verdict AND, more importantly, never ADMISSIBLE.
TRAPS = [
    ("pp_vs_pct",
     "Cut CAC 30%.", "CAC improved 30 percentage points.",
     "NEEDS_REVIEW", "ratio vs percentage-points: same digits, different quantity"),

    ("pp_claim_off_unit",
     "Cut CAC 30 percentage points.", "CAC fell 30%.",
     "NEEDS_REVIEW", "claim stated in a non-canonical unit must not be coerced"),

    ("avg_as_ceiling_cost",
     "Pay as little as $2 per lead.", "Clients averaged $3 per lead.",
     "INADMISSIBLE", "cost-metric ceiling below an average (LOWER_IS_BETTER polarity)"),

    ("cue_far_from_figure",
     "Up to 5-star reviews and a 45% CAC cut.", "Average CAC reduction was 30%.",
     "INADMISSIBLE", "'up to' qualifies '5-star', must not bind to the 45% headline"),

    ("population_mismatch",
     "Join 1,000+ happy clients.", "Over 1,000 customers served.",
     "NEEDS_REVIEW", "clients != customers; refuse to reconcile distinct populations"),

    ("period_inflation",
     "Save $5,000/year.", "Members saved $120/month.",
     "INADMISSIBLE", "$5,000/yr claimed against $1,440/yr ($120×12) after Rule C"),

    ("rounding_just_over",
     "Cut CAC 30%.", "CAC fell 29.4%.",
     "INADMISSIBLE", "30 exceeds 29.4 beyond the half-point asymmetric tolerance"),

    ("no_source_for_metric",
     "Cut CAC 50%.", "We are a Kansas City agency founded in 2018.",
     "NEEDS_REVIEW", "no source value for the claimed metric; never fabricate one"),

    ("guaranteed_floor_no_evidence",
     "Guaranteed 30%+ lower CAC.", "On average, CAC dropped 30%.",
     "NEEDS_REVIEW", "a '+' floor claim needs floor evidence; an average is not a floor"),
]

# Legitimate claims — the suite must not reject these, or "never ADMISSIBLE" is vacuous.
CONTROLS = [
    ("rounding_within_tol", "Cut CAC 30%.", "CAC fell 29.6%.",
     "within the half-point rounding allowance"),
    ("conservative_after_scaling", "Save $1,000/year.", "Members saved $120/month.",
     "$1,000/yr is conservative vs $1,440/yr"),
    ("exact_match", "Cut CAC 30%.", "We reduced CAC by 30%.",
     "claim equals source"),
]


@pytest.mark.parametrize("tid,claim,source,expected,lie", TRAPS, ids=[t[0] for t in TRAPS])
def test_trap_never_certified(tid, claim, source, expected, lie):
    """The cardinal invariant: a deceptive pair is never sealed as ADMISSIBLE."""
    result = _verdict(claim, source)["result"]
    assert result != "ADMISSIBLE", f"{tid} CERTIFIED a deceptive claim ({lie}): {result}"


@pytest.mark.parametrize("tid,claim,source,expected,lie", TRAPS, ids=[t[0] for t in TRAPS])
def test_trap_expected_verdict(tid, claim, source, expected, lie):
    """Tighter: pin the *specific* safe verdict (abstain vs refuse) so regressions show."""
    result = _verdict(claim, source)["result"]
    assert result == expected, f"{tid}: expected {expected}, got {result} ({lie})"


@pytest.mark.parametrize("cid,claim,source,why", CONTROLS, ids=[c[0] for c in CONTROLS])
def test_control_admissible(cid, claim, source, why):
    result = _verdict(claim, source)["result"]
    assert result == "ADMISSIBLE", f"{cid}: legitimate claim wrongly refused ({why}): {result}"


# --- aspiration policy (was the open gap; now enforced) ----------------------
# Source mixes real evidence (avg 30%) with a goal ("aim as high as 60%"). The goal
# figure must not be read as evidence; demote_if_aspirational relabels it ASPIRATIONAL
# and the core abstains. Policy choice: ABSTAIN (NEEDS_REVIEW), never strip-and-guess.
def test_aspiration_not_certified():
    result = _verdict("Up to 45% lower CAC.",
                      "On average clients cut CAC 30%, and we aim as high as 60%.")["result"]
    assert result == "NEEDS_REVIEW", result


def test_achievement_overrides_goal():
    """The two-list design: a figure stated as ACHIEVED is evidence even with a
    co-located goal word, so a genuinely-supported claim still certifies."""
    result = _verdict("Up to 45% lower CAC.",
                      "We aimed high and achieved a 60% CAC reduction.")["result"]
    assert result == "ADMISSIBLE", result
