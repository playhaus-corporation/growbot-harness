"""
gate.py · admissibility gate
============================

Runs conditions C1–C3 per claim and combines them into a verdict, producing the
exact `findings.claims[]` shape that certificate.build_certificate consumes.

Judges (the semantic part of C1):
  - ClaudeJudge   production: one temp-0, structured-JSON entailment call. Written
                  here as the integration point; not exercised offline (no network).
  - LexicalJudge  offline/test stand-in: transparent content-word overlap. It is a
                  STAND-IN so the pipeline runs end-to-end without a key — NOT the
                  production judgment. Clearly labelled wherever it's used.

Verdict per claim:
  any condition FAIL          -> NOT_ADMISSIBLE
  else any NEEDS_REVIEW       -> NEEDS_REVIEW   (blocks mint; routed to a human)
  else                        -> ADMISSIBLE
"""

from __future__ import annotations

from pathlib import Path
import re

import conditions as C

PROMPT_PATH = Path(__file__).parent / "prompts" / "gate_prompt.txt"

_STOP = {"the", "a", "an", "in", "for", "to", "of", "and", "or", "we", "our", "you",
         "your", "with", "on", "at", "by", "is", "are", "see", "get", "that", "this"}


class LexicalJudge:
    """Transparent overlap-based entailment estimate. STAND-IN for the LLM judge."""
    def __init__(self, threshold: float = 0.45):
        self.threshold = threshold

    @staticmethod
    def _content(text: str) -> set[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        return {w.rstrip("s") for w in words if w not in _STOP and len(w) > 2}

    def entails(self, source: str, claim: str):
        cw, sw = self._content(claim), self._content(source)
        if not cw:
            return False, 0.0, "empty claim"
        coverage = len(cw & sw) / len(cw)
        ok = coverage >= self.threshold
        return ok, coverage, (f"[lexical stand-in] {len(cw & sw)}/{len(cw)} claim terms grounded "
                              f"(coverage {coverage:.2f}, threshold {self.threshold})")


class ClaudeJudge:
    """
    Production judge: a single temp-0 entailment call returning strict JSON.
    Not run offline. Fingerprint (model, prompt hash, temperature) is recorded in
    the certificate so the judgment is reproducible.
    """
    DEFAULT_MODEL = "claude-sonnet-4-6"
    PROMPT = PROMPT_PATH.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")

    def __init__(self, client, model: str = DEFAULT_MODEL):
        self.client, self.model = client, model
        self.last_usage = None

    def entails(self, source: str, claim: str):
        import json
        prompt = self.PROMPT.replace("{{SOURCE}}", source).replace("{{CLAIM}}", claim)
        msg = self.client.messages.create(
            model=self.model, max_tokens=400, temperature=0,
            messages=[{"role": "user", "content": prompt}])
        self.last_usage = getattr(msg, "usage", None)
        data = json.loads("".join(b.text for b in msg.content if b.type == "text"))
        return bool(data["entailed"]), float(data["confidence"]), data["reason"]


def assess_claim(claim_id: str, claim_text: str, source_span: str, judge=None) -> dict:
    c1 = C.check_c1_source_grounding(claim_text, source_span, judge)
    c2 = C.check_c2_conservation(claim_text, source_span)
    c3 = C.check_c3_bounded_scope(claim_text, source_span)
    results = [c1["result"], c2["result"], c3["result"]]
    if "FAIL" in results:
        verdict = "NOT_ADMISSIBLE"
    elif "NEEDS_REVIEW" in results:
        verdict = "NEEDS_REVIEW"
    else:
        verdict = "ADMISSIBLE"
    return {
        "claimId": claim_id,
        "claimText": claim_text,
        "sourceRef": {"name": "source", "span": source_span},
        "conditions": {"C1_sourceGrounding": c1, "C2_conservation": c2, "C3_boundedScope": c3},
        "claimResult": verdict,
    }


def run_gate(claims: list[dict], judge=None) -> dict:
    """claims: [{claimId, claimText, sourceSpan}]. Returns {claims, verdict}."""
    assessed = [assess_claim(c["claimId"], c["claimText"], c["sourceSpan"], judge) for c in claims]
    admissible = sum(1 for c in assessed if c["claimResult"] == "ADMISSIBLE")
    overall = "ADMISSIBLE" if assessed and all(c["claimResult"] == "ADMISSIBLE" for c in assessed) else "BLOCKED"
    return {"claims": assessed, "verdict": {"result": overall,
            "claimsTotal": len(assessed), "claimsAdmissible": admissible}}


# --------------------------------------------------------------------------- #
# 5-sample test set: 3 pass, 2 fail (one subtle, one obvious)
# --------------------------------------------------------------------------- #
SOURCE_31 = "Case study, Q1 2026: a client's paid-social CAC fell 31% over the engagement."
SOURCE_QUAL = "Playhaus specializes in paid social campaigns for agency and service-business clients."

SAMPLES = [
    # claimId, claim, source span, expected
    ("s1", "Our clients see a 30%+ reduction in customer acquisition cost.", SOURCE_31, "ADMISSIBLE"),
    ("s2", "Clients get up to 50% lower CAC.", SOURCE_31, "NOT_ADMISSIBLE"),          # subtle: C2
    ("s3", "We specialize in paid social campaigns for agencies.", SOURCE_QUAL, "ADMISSIBLE"),
    ("s4", "We guarantee a 30%+ CAC reduction for every client.", SOURCE_31, "NOT_ADMISSIBLE"),  # obvious: C3 only
    ("s5", "We reduced one client's CAC by 31%.", SOURCE_31, "ADMISSIBLE"),
]

if __name__ == "__main__":
    judge = LexicalJudge()
    print(f"{'id':<3}{'expect':<16}{'got':<16}{'ok':<4}failing condition / reason")
    print("-" * 100)
    all_ok = True
    results = []
    for cid, claim, src, expected in SAMPLES:
        a = assess_claim(cid, claim, src, judge)
        got = a["claimResult"]
        ok = got == expected
        all_ok &= ok
        results.append(a)
        why = ""
        for k, v in a["conditions"].items():
            if v["result"] in ("FAIL", "NEEDS_REVIEW"):
                why = f"{k}: {v['reason']}"
                break
        if not why:  # show the C2 reason on passes for visibility
            why = a["conditions"]["C2_conservation"]["reason"]
        print(f"{cid:<3}{expected:<16}{got:<16}{'Y' if ok else 'N':<4}{why}")

    print("-" * 100)
    print("ALL EXPECTED OUTCOMES MET:", all_ok)

    # Close the loop: an ADMISSIBLE claim flows into the certificate unchanged.
    try:
        import certificate as cert_mod
        good = [r for r in results if r["claimResult"] == "ADMISSIBLE"][0]
        cert = cert_mod.build_certificate(
            asset_text=good["claimText"], asset_id="ad-demo", media_type="text/plain",
            sources=[{"name": "source", "sha256": cert_mod.sha256_text_normalized(good["sourceRef"]["span"]), "uri": None}],
            method={"model": "lexical-standin", "promptTemplateHash": "sha256:demo", "temperature": 0,
                    "embeddingModel": None, "tolerances": {}},
            claims=[good],
            license_terms={"terms": "paid social; US; through 2026-12-31"},
            approver="0xDEMO")
        cert_mod.finalize(cert)
        print("certificate built from gate output; integrity ok:", cert_mod.verify_integrity(cert))
    except Exception as e:
        print("loop check skipped:", e)
