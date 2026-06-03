"""
verify.extract · the extraction boundary (the LLM lives here, and ONLY here)
============================================================================

A Judge turns free text into a structured (ClaimRecord, SourceRecord). It is the
*untrusted* part of the system: it never returns a verdict, only numbers, and the
deterministic core decides. Two implementations:

  LexicalJudge  offline, deterministic regex/parse stand-in. Used by the test suite
                and `--offline` runs. No network, no keys, importable anywhere.
  ClaudeJudge   the real extractor. Calls the model N=3 times at low-but-nonzero
                temperature so runs can differ, feeding the extraction-agreement
                gate. Prompted to emit ONLY the structured tuple as JSON — never a
                verdict, never PASS/FAIL.

This module imports with no API key present; ClaudeJudge only needs one when called.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .config import Config
from .deterministic import (
    ASPIRATIONAL,
    AVERAGE,
    MAX_OBSERVED,
    MIN_OBSERVED,
    POINT,
    RANGE,
    ClaimRecord,
    Cmp,
    SourceRecord,
)

# --------------------------------------------------------------------------- #
# Lexical (deterministic) extraction
# --------------------------------------------------------------------------- #
_NUM = r"\d[\d,]*(?:\.\d+)?"

_UPPER_CUES = ("up to", "as much as", "as high as", "as low as", "as little as")
_LOWER_CUES = ("at least", "or more", "more than", "over ", "north of", "upwards of", "minimum")

_AVERAGE_CUES = ("average", "on average", "avg", "mean")
_MIN_CUES = ("at least", "minimum", "at minimum", "no fewer", "guaranteed floor")
_MAX_CUES = ("as high as", "peaked", "maximum", "max ", "as much as")

# A trailing "+" that turns a number into a floor: "1,000+", "30%+", "$5 +".
# NOTE: must allow an optional unit char (e.g. "%") BETWEEN the digits and the "+",
# or "30%+" is silently read as a POINT and a guaranteed-floor claim sails through.
_PLUS_FLOOR = re.compile(r"\d[\d,]*\s*%?\s*\+")

# A figure near a GOAL cue is an aspiration, not evidence; a figure near an ACHIEVED
# cue is a real result. When both sit by the same figure, achievement wins (the copy
# says it actually happened). Policy: a goal figure -> abstain (NEEDS_REVIEW).
_ASPIRATION_CUES = (
    "aim", "target", "goal", "projected", "on track", "designed to",
    "potential", "could see",
)
_ACHIEVEMENT_CUES = ("saw", "achieved", "accomplished", "succeeded")


def _aspirational(window_text: str) -> bool:
    """True if the figure is framed as a goal AND not asserted as an achieved result."""
    goal = any(cue in window_text for cue in _ASPIRATION_CUES)
    achieved = any(cue in window_text for cue in _ACHIEVEMENT_CUES)
    return goal and not achieved


def demote_if_aspirational(source: SourceRecord) -> SourceRecord:
    """
    Judge-independent deterministic guard. Applied in run.verify_claim to ANY judge's
    output (LexicalJudge or ClaudeJudge), it re-scans the recorded source_span around
    the strongest figure: if that figure is a goal and not an achieved result, relabel
    the source ASPIRATIONAL so the core abstains. It re-derives this from the span, so
    it never trusts the extractor's own kind classification.
    """
    if source.kind == ASPIRATIONAL:
        return source
    span = source.source_span or ""
    if _aspirational(_window(span, _headline_match(span))):
        return replace(source, kind=ASPIRATIONAL)
    return source


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def _detect_metric(text: str, config: Config) -> str | None:
    """Resolve the canonical metric key from aliases, most specific first."""
    t = text.lower()
    # Priority order matters: a span like "30% reduction in CAC for clients" is a CAC
    # metric, not a client count — the more specific signal wins.
    priority = [
        "cost_per_lead_usd",
        "cac_reduction_pct",
        "annual_savings_usd",
        "clients_count",
        "customers_count",
    ]
    for metric in priority:
        if metric not in config.metrics:
            continue
        for alias in config.metrics[metric].get("aliases", []):
            if alias.lower() in t:
                return metric
    return None


def _detect_unit(text: str, metric: str, config: Config) -> str:
    """Pick the unit as *written*, so normalize can catch period/points mismatches."""
    t = text.lower()
    if "percentage point" in t or re.search(r"\bpp\b", t):
        return "pp"            # deliberately NOT "%": Rule C must refuse to conflate them
    if "%" in t or "percent" in t:
        return "%"
    if "$" in t or "usd" in t or "dollar" in t:
        if re.search(r"/\s*(mo|month)\b", t) or "per month" in t:
            return "$/month"
        if re.search(r"/\s*(yr|year)\b", t) or "per year" in t or "annual" in t:
            return "$/year"
        if "/lead" in t or "per lead" in t:
            return "$/lead"
    return config.unit(metric)   # fall back to the metric's canonical unit


def _numbers(text: str) -> list[float]:
    return [_to_float(m) for m in re.findall(_NUM, text)]


def _headline_match(text: str):
    """The regex match for the largest number — the figure a cue most likely qualifies."""
    matches = list(re.finditer(_NUM, text))
    return max(matches, key=lambda m: _to_float(m.group())) if matches else None


def _window(text: str, headline, before: int = 25, after: int = 10) -> str:
    """
    Lowercased text *around the headline figure*. A cue like "average" or "up to"
    only counts when it sits next to the number it qualifies — not anywhere in the
    sentence. This is the guard against a DETERMINISTIC mis-extraction: the agreement
    gate can't catch one (the same wrong read agrees with itself 3×), so the read has
    to be bound to the figure. Mirrors conditions.parse_quantities' ±25-char window.
    """
    if headline is None:
        return text.lower()
    return text[max(0, headline.start() - before): headline.end() + after].lower()


def _detect_comparator(text: str, headline=None) -> Cmp:
    win = _window(text, headline)
    if any(cue in win for cue in _UPPER_CUES):
        return Cmp.UPPER_BOUND
    # a trailing "+" on the number ("1,000+", "30%+") or an explicit floor phrase
    if _PLUS_FLOOR.search(win) or any(cue in win for cue in _LOWER_CUES):
        return Cmp.LOWER_BOUND
    return Cmp.POINT


def _detect_source_kind(text: str, headline=None) -> str:
    t = text.lower()
    # RANGE is a structural two-number pattern ("18% to 30%", "18–30%") — its evidence
    # is the pair itself, so it is safe (and necessary) to detect across the full text.
    if re.search(rf"{_NUM}\s*(?:%|percent)?\s*(?:to|-|–|—)\s*{_NUM}", t) or "ranged" in t or "ranging" in t:
        return RANGE
    # Bound / average cues, by contrast, only mean something next to the figure.
    win = _window(text, headline)
    if any(cue in win for cue in _AVERAGE_CUES):
        return AVERAGE
    if any(cue in win for cue in _MIN_CUES) or _PLUS_FLOOR.search(win):
        return MIN_OBSERVED
    if any(cue in win for cue in _MAX_CUES):
        return MAX_OBSERVED
    return POINT


class LexicalJudge:
    """
    Deterministic, offline extraction stand-in. Transparent regex/parse — NOT the
    production read. Same text in → same records out, every time, so it both seeds
    the test suite and lets the agreement gate run without a model.
    """

    def __init__(self, config: Config | None = None):
        self.config = config or Config.load()

    def _claim(self, claim_text: str) -> ClaimRecord:
        metric = _detect_metric(claim_text, self.config)
        if metric is None:
            # Unknown metric: still emit a record so normalize can abstain cleanly.
            return ClaimRecord("unknown", self.config.polarity("cac_reduction_pct"),
                               float("nan"), Cmp.POINT, "?", claim_text)
        nums = _numbers(claim_text)
        value = max(nums) if nums else float("nan")
        headline = _headline_match(claim_text)
        return ClaimRecord(
            metric=metric,
            polarity=self.config.polarity(metric),
            value=value,
            comparator=_detect_comparator(claim_text, headline),
            unit=_detect_unit(claim_text, metric, self.config),
            claim_span=claim_text.strip(),
        )

    def _source(self, source_text: str) -> SourceRecord:
        metric = _detect_metric(source_text, self.config)
        headline = _headline_match(source_text)
        kind = _detect_source_kind(source_text, headline)
        nums = _numbers(source_text)
        if kind == RANGE and len(nums) >= 2:
            value: float | list[float] = [min(nums), max(nums)]
        else:
            value = max(nums) if nums else float("nan")
        # Aspiration ("aim as high as 60%") is demoted to NEEDS_REVIEW by the
        # judge-independent guard demote_if_aspirational(), applied in run.verify_claim.
        ref_metric = metric or "unknown"
        unit = _detect_unit(source_text, metric, self.config) if metric else "?"
        return SourceRecord(
            metric=ref_metric,
            value=value,
            kind=kind,
            unit=unit,
            source_span=source_text.strip(),
        )

    def extract(self, claim_text: str, source_text: str) -> tuple[ClaimRecord, SourceRecord]:
        return self._claim(claim_text), self._source(source_text)

    def extract_n(self, claim_text: str, source_text: str, n: int = 3):
        """Deterministic, so the n runs are identical and the agreement gate passes."""
        one = self.extract(claim_text, source_text)
        return [one for _ in range(n)]


# --------------------------------------------------------------------------- #
# Claude (production) extraction — records only, never a verdict
# --------------------------------------------------------------------------- #
EXTRACTION_PROMPT = """You extract structured numeric records from advertising text. \
You do NOT judge, verify, or decide PASS/FAIL — you only report what the text says.

Given a CLAIM and its cited SOURCE, return a single JSON object, no prose, no code fence:

{
  "claim":  {"metric": str, "value": number, "comparator": "POINT|UPPER_BOUND|LOWER_BOUND", "unit": str, "span": str},
  "source": {"metric": str, "value": number, "kind": "POINT|RANGE|MAX_OBSERVED|MIN_OBSERVED|AVERAGE|ASPIRATIONAL", "unit": str, "span": str}
}

Rules:
- "up to X" -> comparator UPPER_BOUND. "at least X"/"X+"/"over X" -> LOWER_BOUND. A bare X -> POINT.
- For a RANGE source, set "value" to [lo, hi].
- Use the unit exactly as written ("%", "pp" for percentage points, "$/month", "$/year", "$/lead", or a count noun).
- Never conflate "%" with "percentage points".
- Distinguish a GOAL from a RESULT. If the source figure is framed as a target or aspiration
  ("aim", "target", "goal", "projected", "on track to", "designed to", "potential", "could reach/see"),
  set the source "kind" to "ASPIRATIONAL" — it is NOT evidence. Words like "saw", "achieved",
  "accomplished", "succeeded", "reached" mark an achieved result (use POINT/AVERAGE/MAX_OBSERVED/etc.).
  A figure that is both aimed-for AND achieved counts as achieved.
- Put the exact quoted source text in "span" (including any goal/achieved wording) — a deterministic
  guard re-reads it, so the span must contain the words that justify the kind.
- metric is a short snake_case key naming the quantity (e.g. cac_reduction_pct, annual_savings_usd, clients_count).
- Report only. The verdict is computed elsewhere by a deterministic function.

CLAIM:
{{CLAIM}}

SOURCE:
{{SOURCE}}"""

_CMP_MAP = {"POINT": Cmp.POINT, "UPPER_BOUND": Cmp.UPPER_BOUND, "LOWER_BOUND": Cmp.LOWER_BOUND}
_KIND_SET = {POINT, RANGE, MAX_OBSERVED, MIN_OBSERVED, AVERAGE, ASPIRATIONAL}


class ClaudeJudge:
    """
    Production extractor. N=3 samples at low-but-nonzero temperature so the runs can
    genuinely differ; the deterministic agreement gate turns any disagreement into
    NEEDS_REVIEW. The model is asked for records only — the verdict is never its call.
    """

    DEFAULT_MODEL = "claude-sonnet-4-6"
    PROMPT = EXTRACTION_PROMPT

    def __init__(self, client, config: Config | None = None,
                 model: str = DEFAULT_MODEL, temperature: float = 0.4, n: int = 3):
        self.client = client
        self.config = config or Config.load()
        self.model = model
        self.temperature = temperature
        self.n = n
        self.last_usage = None   # aggregate token usage across the N extraction calls

    @staticmethod
    def _strip_fence(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        return text.strip()

    def _parse(self, payload: str) -> tuple[ClaimRecord, SourceRecord]:
        import json

        data = json.loads(self._strip_fence(payload))
        c, s = data["claim"], data["source"]
        cmp_ = _CMP_MAP[c["comparator"]]
        metric = c["metric"]
        polarity = self.config.polarity(metric) if self.config.known_metric(metric) \
            else self.config.polarity("cac_reduction_pct")
        claim = ClaimRecord(metric, polarity, float(c["value"]), cmp_, c["unit"], c.get("span", ""))
        kind = s["kind"] if s["kind"] in _KIND_SET else POINT
        sval = s.get("value")
        if isinstance(sval, list):
            sval = [float(x) for x in sval]
        elif sval is None:                 # e.g. an ASPIRATIONAL goal with no observed figure
            sval = float("nan")
        else:
            sval = float(sval)
        source = SourceRecord(s["metric"], sval, kind, s["unit"], s.get("span", ""))
        return claim, source

    def extract_n(self, claim_text: str, source_text: str, n: int | None = None):
        prompt = self.PROMPT.replace("{{CLAIM}}", claim_text).replace("{{SOURCE}}", source_text)
        runs = []
        in_tok = out_tok = calls = 0
        for _ in range(n or self.n):
            msg = self.client.messages.create(
                model=self.model, max_tokens=600, temperature=self.temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            usage = getattr(msg, "usage", None)
            if usage is not None:
                in_tok += getattr(usage, "input_tokens", 0) or 0
                out_tok += getattr(usage, "output_tokens", 0) or 0
            calls += 1
            payload = "".join(b.text for b in msg.content if b.type == "text")
            runs.append(self._parse(payload))
        self.last_usage = {"input_tokens": in_tok, "output_tokens": out_tok, "calls": calls}
        return runs

    def extract(self, claim_text: str, source_text: str):
        return self.extract_n(claim_text, source_text, 1)[0]
