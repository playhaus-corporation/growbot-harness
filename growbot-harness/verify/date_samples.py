"""
verify.date_samples · demo fixtures for the certificate validity window
=======================================================================

Three certificates that exercise the time-in-force logic (certificate.check_validity):

    d1 -> VALID          a year-long certificate, 30 days in
    d2 -> EXPIRED        a certificate that lapsed last month
    d3 -> NOT_YET_VALID  a pre-dated certificate that takes effect later

These are SEPARATE from verify.samples (s1-s6), which feed the *gate* (claim vs.
source). A date sample instead fixes a certificate's validity *window* (X.509-style
notBefore/notAfter) and asks what check_validity returns *now*.

Windows are defined RELATIVE to the evaluation time, not as absolute dates: an
absolute "validUntil 2025-01-01" would silently change category as the clock moves,
so a "VALID" fixture would rot into "EXPIRED". Offsets keep each sample's meaning
stable whenever it is built. build_dated_cert(as_of=...) injects the reference time
so the result is deterministic and testable.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import certificate

# id -> (label, expected_status, start_offset_days, end_offset_days)  [offsets from as_of]
DATE_SAMPLES: dict[str, tuple[str, str, int, int]] = {
    "d1": ("a year-long certificate, 30 days in",      "VALID",          -30, 335),
    "d2": ("a certificate that lapsed last month",     "EXPIRED",       -400, -35),
    "d3": ("a pre-dated certificate, effective later", "NOT_YET_VALID",   30, 395),
}

# A known-admissible claim (mirrors verify.samples s1) so each dated cert is coherent:
# the date samples vary only the window, not the admissibility verdict.
_CLAIM_TEXT = "We cut CAC 30%."
_SOURCE_NAME = "source.txt"
_SOURCE_TEXT = "We reduced CAC by 30%."

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "samples"


def date_sample(sample_id: str) -> tuple[str, str, int, int]:
    if sample_id not in DATE_SAMPLES:
        raise KeyError(f"unknown date sample '{sample_id}'; known: {', '.join(DATE_SAMPLES)}")
    return DATE_SAMPLES[sample_id]


def build_dated_cert(sample_id: str, as_of: datetime | None = None) -> dict:
    """Build + finalize an ADMISSIBLE certificate whose validity window realizes the
    sample's expected status when checked at `as_of` (default: now)."""
    label, _expected, start_off, end_off = date_sample(sample_id)
    ref = as_of or datetime.now(timezone.utc)
    valid_from = certificate.iso(ref + timedelta(days=start_off))
    valid_until = certificate.iso(ref + timedelta(days=end_off))

    claims = [{
        "claimId": sample_id,
        "claimText": _CLAIM_TEXT,
        "sourceRef": {"name": _SOURCE_NAME, "span": _SOURCE_TEXT},
        "claimResult": "ADMISSIBLE",
    }]
    cert = certificate.build_certificate(
        asset_text=_CLAIM_TEXT,
        asset_id=sample_id,
        media_type="text/plain",
        sources=[{
            "name": _SOURCE_NAME,
            "sha256": certificate.sha256_text_normalized(_SOURCE_TEXT),
            "uri": None,
        }],
        method={"model": "lexical-standin (offline)", "promptTemplateHash": "sha256:none", "temperature": 0},
        claims=claims,
        license_terms={"terms": f"paid social; US; {label}", "validFrom": valid_from, "validUntil": valid_until},
        approver="0xDEMO00000000000000000000000000000000bEEF",
    )
    return certificate.finalize(cert)


def write_fixtures(out_dir: Path = _FIXTURE_DIR) -> list[Path]:
    """Regenerate the three date-sample cert JSONs (relative windows -> fresh each run).
    Drop one into the Streamlit verify page to see its validity status line."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for sid, (_label, expected, *_off) in DATE_SAMPLES.items():
        cert = build_dated_cert(sid)
        path = out_dir / f"date_{sid}_{expected.lower()}.cert.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(cert, f, indent=2, ensure_ascii=False)
            f.write("\n")
        written.append(path)
    return written


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    print(f"as_of: {certificate.iso(now)}  (asset='{_CLAIM_TEXT}')\n")
    all_ok = True
    for sid, (label, expected, _a, _b) in DATE_SAMPLES.items():
        cert = build_dated_cert(sid, as_of=now)
        window = cert["license"]["validity"]
        rep = certificate.check_validity(cert, now)
        ok = rep["status"] == expected
        all_ok = all_ok and ok
        print(f"{sid}  {label}")
        print(f"    window : {window['notBefore']} -> {window['notAfter']}")
        print(f"    status : {rep['status']:<14} expected {expected}   {'OK' if ok else 'FAIL'}")
    paths = write_fixtures()
    print("\nwrote: " + ", ".join(p.name for p in paths))
    print("ALL EXPECTED STATUSES MET:", all_ok)
