"""
verify.samples · canonical demo fixtures (the prompt's authoritative s1-s6 table)
=================================================================================

These are verify-layer fixtures keyed by `s1..s6`. They are intentionally separate
from `gate.SAMPLES` and `samples/*.txt`, which reuse IDs for the older C1-C3 flow.
"""

from __future__ import annotations

# id -> (claim_text, source_text, expected_verdict, rule)
SAMPLES: dict[str, tuple[str, str, str, str]] = {
    "s1": ("We cut CAC 30%.",
           "We reduced CAC by 30%.",
           "ADMISSIBLE", "A"),
    "s2": ("We cut CAC 40%.",
           "We reduced CAC by 30%.",
           "INADMISSIBLE", "A"),
    "s3": ("Get up to 50% lower CAC.",
           "On average, clients saw a 30% reduction in CAC.",
           "INADMISSIBLE", "B"),
    "s4": ("Get up to 30% lower CAC.",
           "Across the cohort, CAC reduction ranged from 18% to 30%.",
           "ADMISSIBLE", "B"),
    "s5": ("Saves $5,000/yr.",
           "One client saved $420/month over the engagement.",
           "ADMISSIBLE", "C"),
    "s6": ("Trusted by 1,000+ clients.",
           "We serve over 1,000 customers.",
           "NEEDS_REVIEW", "C"),
}


def sample(sample_id: str) -> tuple[str, str, str, str]:
    if sample_id not in SAMPLES:
        raise KeyError(f"unknown sample '{sample_id}'; known: {', '.join(SAMPLES)}")
    return SAMPLES[sample_id]
