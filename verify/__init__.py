"""
verify · deterministic quantitative-claim verification
======================================================

The LLM does *extraction only* (untrusted input). The verdict is a pure function
over the extracted numbers — same inputs → same verdict, with no LLM, clock, RNG,
or network in the verify layer. This package is the gate that decides whether an
ad claim may be minted.

Layout:
  deterministic.py  the pure core: enums, records, more_aggressive, check_claim, gate
  config.py         loads rules.json; supplies per-metric polarity + rounding_tol
  normalize.py      Rule C — period/unit canonicalization + scope reconciliation
  extract.py        the LLM boundary: Judge protocol, LexicalJudge (offline), ClaudeJudge
  run.py            composes extract → gate → normalize → check_claim into one verdict
"""
