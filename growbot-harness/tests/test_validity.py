"""
tests.test_validity · the certificate validity window (time-in-force)
=====================================================================

Covers the expiry / start-end logic added to the certificate:
  - the window is computed from valid_days (or explicit validFrom/validUntil),
  - check_validity is a pure function of (cert, as_of): NOT_YET_VALID / VALID / EXPIRED,
  - the window is part of the signed substance (tamper-evident),
  - notAfterEpoch (-> Story PIL `expiration`) agrees with notAfter.

No network, no keys, no chain. Run from growbot-harness:

    python -m pytest tests/test_validity.py -q
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS))

import certificate as C  # noqa: E402
from verify import date_samples as DS  # noqa: E402


def _cert(*, valid_days=365, license_terms=None):
    claims = [{"claimId": "cl-1", "claimText": "x", "claimResult": "ADMISSIBLE"}]
    cert = C.build_certificate(
        asset_text="ad copy", asset_id="ad-1", media_type="text/plain",
        sources=[{"name": "s.txt", "sha256": C.sha256_text_normalized("src"), "uri": None}],
        method={"model": "m", "promptTemplateHash": "sha256:0", "temperature": 0},
        claims=claims, license_terms=license_terms or {"terms": "t"},
        approver="0xABC", valid_days=valid_days,
    )
    return C.finalize(cert)


def test_window_spans_valid_days():
    cert = _cert(valid_days=90)
    nb = C.parse_iso(cert["license"]["validity"]["notBefore"])
    na = C.parse_iso(cert["license"]["validity"]["notAfter"])
    assert nb is not None
    assert na is not None
    assert round((na - nb).total_seconds() / 86400) == 90


def test_check_validity_three_states():
    cert = _cert(valid_days=30)
    issued = C.parse_iso(cert["header"]["issuedAt"])
    assert issued is not None
    assert C.check_validity(cert, issued)["status"] == "VALID"
    assert C.check_validity(cert, issued + timedelta(days=31))["status"] == "EXPIRED"
    assert C.check_validity(cert, issued - timedelta(days=1))["status"] == "NOT_YET_VALID"


def test_in_force_flag_tracks_status():
    cert = _cert(valid_days=10)
    issued = C.parse_iso(cert["header"]["issuedAt"])
    assert issued is not None
    assert C.check_validity(cert, issued)["inForce"] is True
    assert C.check_validity(cert, issued + timedelta(days=20))["inForce"] is False


def test_explicit_window_override():
    cert = _cert(license_terms={"terms": "t", "validFrom": "2030-01-01", "validUntil": "2030-12-31"})
    v = cert["license"]["validity"]
    assert v["notBefore"] == "2030-01-01T00:00:00Z"
    assert v["notAfter"] == "2030-12-31T00:00:00Z"


def test_pil_epoch_matches_not_after():
    cert = _cert(valid_days=200)
    v = cert["license"]["validity"]
    not_after = C.parse_iso(v["notAfter"])
    assert not_after is not None
    assert v["notAfterEpoch"] == int(not_after.timestamp())


def test_window_is_inside_integrity_hash():
    """Editing the expiry after finalize must break the integrity hash."""
    cert = _cert(valid_days=365)
    assert C.verify_integrity(cert)
    cert["license"]["validity"]["notAfter"] = "2099-01-01T00:00:00Z"
    assert not C.verify_integrity(cert), "expiry extension must be tamper-evident"


def test_legacy_cert_with_no_window():
    cert = _cert(valid_days=365)
    del cert["license"]["validity"]
    rep = C.check_validity(cert)
    assert rep["status"] == "NO_WINDOW"
    assert rep["inForce"] is True


def test_empty_window_rejected():
    try:
        _cert(license_terms={"terms": "t", "validFrom": "2030-06-01", "validUntil": "2030-01-01"})
    except ValueError:
        return
    raise AssertionError("an end-before-start window should raise")


# --- date_samples (d1 VALID / d2 EXPIRED / d3 NOT_YET_VALID) -----------------
def test_date_samples_realize_expected_status():
    """Each date sample lands in its category when checked at the same reference time."""
    ref = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
    for sid, (_label, expected, _a, _b) in DS.DATE_SAMPLES.items():
        cert = DS.build_dated_cert(sid, as_of=ref)
        assert C.check_validity(cert, ref)["status"] == expected, sid


def test_date_samples_cover_all_three_states():
    assert {exp for _l, exp, _a, _b in DS.DATE_SAMPLES.values()} == {
        "VALID", "EXPIRED", "NOT_YET_VALID",
    }


def test_date_samples_stable_across_reference_times():
    """Relative windows keep their meaning at a different 'now' -- they don't rot."""
    for ref in (
        datetime(2026, 6, 2, tzinfo=timezone.utc),
        datetime(2031, 1, 15, tzinfo=timezone.utc),
    ):
        for sid, (_label, expected, _a, _b) in DS.DATE_SAMPLES.items():
            cert = DS.build_dated_cert(sid, as_of=ref)
            assert C.check_validity(cert, ref)["status"] == expected, (sid, ref)
