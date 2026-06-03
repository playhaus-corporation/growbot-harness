"""
verify.config · versioned tolerance + metric config (stdlib only)
=================================================================

Tolerances are *config, not code*, and the ruleVersion goes in the certificate so
the verdict is reproducible: a re-run on a different version is a different check,
and the cert says so. The deterministic core reads polarity/tol from here; nothing
in the core hard-codes a metric.

rules.json is JSON (not YAML) on purpose — JSON parses with the standard library,
so config loading keeps the zero-dependency promise the verify layer is built on.
"""

from __future__ import annotations

import json
from pathlib import Path

from .deterministic import Polarity

RULES_PATH = Path(__file__).with_name("rules.json")


class Config:
    """Loaded view over rules.json. Read-only; constructed once per run."""

    def __init__(self, data: dict):
        self.rule_version: str = data["ruleVersion"]
        self.metrics: dict = data["metrics"]

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        with (path or RULES_PATH).open("r", encoding="utf-8") as f:
            return cls(json.load(f))

    def known_metric(self, metric: str) -> bool:
        return metric in self.metrics

    def polarity(self, metric: str) -> Polarity:
        return Polarity[self.metrics[metric]["polarity"]]

    def unit(self, metric: str) -> str:
        return self.metrics[metric]["unit"]

    def precision(self, metric: str) -> int:
        return int(self.metrics[metric].get("precision", 0))

    def period_conversions(self, metric: str) -> dict:
        return self.metrics[metric].get("periodConversions", {})

    def rounding_tol(self, metric: str, claim_value: float | None = None) -> float:
        """
        The asymmetric rounding allowance for a metric.

        A claim may round in its favor by at most *half its least-significant digit*:
        "30%" admissibly represents a source in [29.5, 30.5], so tol = 0.5. The
        derivation is half of one unit in the last place — 0.5 * 10**(-precision).

        We honor the *claim's* own precision when it is finer than the metric's
        canonical precision (a claim of "30.5%" only earns a 0.05 cushion, not 0.5),
        because the cushion is a property of how precisely the claim was *stated*.
        Tolerance is only ever applied in the conservative direction by check_claim;
        this function just sizes it.
        """
        precision = self.precision(metric)
        if claim_value is not None:
            precision = max(precision, _decimal_places(claim_value))
        return 0.5 * (10 ** (-precision))


def _decimal_places(value: float) -> int:
    """Number of significant decimal places in a value, e.g. 30 -> 0, 30.5 -> 1."""
    text = repr(float(value))
    if "e" in text or "E" in text:        # scientific notation: treat as integer-precision
        return 0
    _, _, frac = text.partition(".")
    frac = frac.rstrip("0")
    return len(frac)
