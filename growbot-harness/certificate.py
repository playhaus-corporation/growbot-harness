"""
growbot-harness · Admissibility Certificate
=============================================

The certificate is the interface contract between three components:

    gate.py      -> produces the findings/verdict, builds + finalizes the cert
    register.py  -> pins the finalized cert to IPFS, anchors it on Story
    verify.py    -> the consumer re-tests the cert against the raw asset/sources

DESIGN BASIS (honest scope, read before quoting it anywhere)
------------------------------------------------------------
Modeled on the *verification discipline* of the Structural Coherence — Anchor
Specification (SC-AS) v1.0, Coherence Research (author: Jason Carroll). SC-AS
binds each certificate to a target's identity + SHA-256 at verification time
(SC-SCOPE Sec. 7.4) and computes integrity by blanking the hash field and
hashing the canonical bytes.

This file is an APPLIED-LAYER EXTENSION. It is NOT canonical SC-AS, it is NOT an
RCC, and "ADMISSIBLE" here means "passed our domain-scoped checks" -- NOT true,
good, or legally compliant. Canonical SC-AS terms are deliberately avoided in
field names; do not introduce them without reading SC-CORE.

ZERO-DEPENDENCY VERIFICATION
----------------------------
Everything in this module uses only the Python standard library, so the
consumer can re-run the integrity + input checks without trusting any package.
Signature recovery and chain reads are the only steps that need extra libs;
they live in verify.py, not here.

CANONICALIZATION RULE (applied-layer adaptation of SC-AS Sec. 3.2 Rule 7)
-------------------------------------------------------------------------
The certificate hash (header.integrity.sha256) is computed over the cert with
these fields blanked, because they do not exist yet / are self-referential at
hash time:

    header.integrity.sha256  -> ""     (self-referential)
    approval.signature       -> ""     (you can't sign a hash of the signature)
    approval.signedAt        -> null   (set at signing, alongside the signature)
    anchor                   -> null   (only exists AFTER the mint)

Serialization: json.dumps(obj, sort_keys=True, ensure_ascii=False,
separators=(",", ":")), encoded UTF-8, no trailing newline. Sorted keys +
compact separators make the bytes reproducible regardless of construction order.

Dependency direction is downward, like SC-AS: the chain points to the cert; the
cert does not embed chain state in its signed substance. anchor.ipMetadataHash
MUST equal header.integrity.sha256 once minted.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone

CERT_SPEC_VERSION = "0.1.0"
CONDITIONS_VERSION = "C1-C3 v0.1"

# Fields excluded from the integrity hash, as (path) tuples.
_HASH_EXCLUDED = (
    ("header", "integrity", "sha256"),
    ("approval", "signature"),
    ("approval", "signedAt"),
    ("anchor",),
)


# --------------------------------------------------------------------------- #
# Hashing primitives (stdlib only)
# --------------------------------------------------------------------------- #
def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex of raw bytes. Use this on the exact asset/source bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """SHA-256 hex of text, normalized to UTF-8 with LF line endings."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return sha256_bytes(normalized.encode("utf-8"))


def sha256_text_normalized(text: str) -> str:
    """
    Hash text the way the verifier recomputes it: LF-normalized and stripped.
    This is the single normalization the asset/source hashes MUST use, so that
    build (certificate) and verify (input recompute) agree byte-for-byte.
    """
    return sha256_text(text.replace("\r\n", "\n").replace("\r", "\n").strip())


def sha256_declared_text_bytes(data: bytes) -> str:
    """Hash text input bytes the same way cli.py reads ad and source text."""
    return sha256_text_normalized(data.decode("utf-8"))


def _canonical_bytes(cert: dict) -> bytes:
    """Serialize the cert deterministically for hashing, with excluded fields blanked."""
    c = copy.deepcopy(cert)
    for path in _HASH_EXCLUDED:
        _blank_path(c, path)
    return json.dumps(
        c, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _blank_path(obj: dict, path: tuple) -> None:
    """Set the value at a nested path to "" (leaf) / None (whole subtree). Tolerant of missing keys."""
    if len(path) == 1:
        if path[0] in obj:
            obj[path[0]] = None if path[0] == "anchor" else ""
        return
    node = obj
    for key in path[:-1]:
        if not isinstance(node.get(key), dict):
            return
        node = node[key]
    if path[-1] in node:
        node[path[-1]] = ""


def compute_cert_hash(cert: dict) -> str:
    """The certificate's integrity hash, per the canonicalization rule."""
    return sha256_bytes(_canonical_bytes(cert))


def finalize(cert: dict) -> dict:
    """Stamp header.integrity.sha256. Call after the approver signs (or before, if unsigned)."""
    cert["header"]["integrity"]["sha256"] = compute_cert_hash(cert)
    return cert


# --------------------------------------------------------------------------- #
# Verification (what verify.py wraps for the consumer)
# --------------------------------------------------------------------------- #
def verify_integrity(cert: dict) -> bool:
    """True iff the stamped hash matches a recomputation. Detects any tampering with signed substance."""
    stamped = cert.get("header", {}).get("integrity", {}).get("sha256", "")
    return bool(stamped) and stamped == compute_cert_hash(cert)


def verify_against_inputs(cert: dict, asset_bytes: bytes,
                          sources: dict[str, bytes]) -> dict:
    """
    Re-test the cert against the raw inputs the consumer holds. This is the
    'test, don't trust' core: recompute hashes from bytes and compare to the cert.
    Returns a structured report; does not raise on mismatch.
    """
    report = {"integrityOk": verify_integrity(cert), "asset": {}, "sources": [], "ok": False}

    want_asset = cert["subject"]["sha256"]
    got_asset = sha256_declared_text_bytes(asset_bytes)
    report["asset"] = {"expected": want_asset, "actual": got_asset, "match": want_asset == got_asset}

    declared = {s["name"]: s["sha256"] for s in cert.get("sources", [])}
    all_sources_ok = set(declared) == set(sources)
    for name, want in declared.items():
        if name in sources:
            got = sha256_declared_text_bytes(sources[name])
            match = got == want
        else:
            got, match = None, False
        all_sources_ok = all_sources_ok and match
        report["sources"].append({"name": name, "expected": want, "actual": got, "match": match})

    report["ok"] = report["integrityOk"] and report["asset"]["match"] and all_sources_ok
    return report


# --------------------------------------------------------------------------- #
# Construction helper (gate.py calls this with its findings)
# --------------------------------------------------------------------------- #
def build_certificate(*, asset_text: str, asset_id: str, media_type: str,
                      sources: list[dict], method: dict, claims: list[dict],
                      license_terms: dict, approver: str) -> dict:
    """
    Assemble an unsigned, unstamped certificate from gate output.

    `claims` items: {claimId, claimText, sourceRef, conditions:{C1_..,C2_..,C3_..}, claimResult}
    `sources` items: {name, sha256, uri?}
    A certificate is only built for ADMISSIBLE assets; the gate returns the same
    findings/verdict shape (un-signed, un-anchored) on the FAIL path and skips this.
    """
    admissible = sum(1 for c in claims if c["claimResult"] == "ADMISSIBLE")
    verdict = "ADMISSIBLE" if admissible == len(claims) and claims else "NOT_ADMISSIBLE"
    if verdict != "ADMISSIBLE":
        raise ValueError("build_certificate is for ADMISSIBLE assets only; got NOT_ADMISSIBLE")

    return {
        "header": {
            "specName": "growbot Admissibility Certificate (applied-layer, modeled on SC-AS)",
            "specVersion": CERT_SPEC_VERSION,
            "documentClass": "APPLIED-CERT",  # NOT a canonical SC-AS document class
            "basis": "SC-AS v1.0 verification discipline, Coherence Research (J. Carroll)",
            "basisScope": "applied-layer extension; not canonical SC-AS; not an RCC; admissibility != compliance",
            "issuer": "growbot-harness",
            "issuedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "integrity": {
                "alg": "sha256",
                "canon": "json:sort_keys,utf8,compact;exclude=header.integrity.sha256,approval.signature,approval.signedAt,anchor",
                "sha256": "",
            },
        },
        "subject": {
            "assetId": asset_id,
            "mediaType": media_type,
            "sha256": sha256_text_normalized(asset_text),
            "contentUri": None,
        },
        "sources": sources,
        "method": {
            "model": method["model"],
            "promptTemplateHash": method["promptTemplateHash"],
            "temperature": method.get("temperature", 0),
            "embeddingModel": method.get("embeddingModel"),
            "conditionsVersion": CONDITIONS_VERSION,
            "tolerances": method.get("tolerances", {}),
        },
        "findings": {"claims": claims},
        "verdict": {"result": verdict, "claimsTotal": len(claims), "claimsAdmissible": admissible},
        "license": {
            "framework": "Story PIL",
            "terms": license_terms.get("terms"),
            "licenseTermsId": None,  # filled after PIL attach
        },
        "approval": {
            "approver": approver,    # wallet address; signs header.integrity.sha256
            "signature": "",         # excluded from hash; filled at signing
            "signedAt": None,        # excluded from hash; filled at signing
        },
        "anchor": None,              # excluded from hash; filled post-mint
    }


def attach_anchor(cert: dict, *, chain_id: int, ip_id: str, nft_contract: str,
                  token_id: str, license_terms_id: str, ip_metadata_cid: str,
                  tx_hash: str) -> dict:
    """Record on-chain coordinates AFTER minting. ipMetadataHash must equal the cert hash."""
    cert["anchor"] = {
        "chainId": chain_id,            # Story Aeneid testnet -- CONFIRM the id before relying on it
        "ipId": ip_id,
        "nftContract": nft_contract,
        "tokenId": token_id,
        "licenseTermsId": license_terms_id,
        "ipMetadataCid": ip_metadata_cid,
        "ipMetadataHash": cert["header"]["integrity"]["sha256"],
        "txHash": tx_hash,
    }
    return cert


# --------------------------------------------------------------------------- #
# Self-test: build -> finalize -> verify -> tamper -> detect
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    SOURCE = "Case study, Q1 2026: client paid-social CAC fell 31% over the engagement.\n"
    ASSET = "Our clients see a 30%+ reduction in customer acquisition cost.\n"

    sources = [{"name": "case-study-q1-2026.md", "sha256": sha256_text_normalized(SOURCE), "uri": None}]
    claims = [{
        "claimId": "cl-1",
        "claimText": "Our clients see a 30%+ reduction in customer acquisition cost.",
        "sourceRef": {"name": "case-study-q1-2026.md", "span": "CAC fell 31% over the engagement"},
        "conditions": {
            "C1_sourceGrounding": {"result": "PASS", "score": 0.96, "reason": "Entailed by cited span (31% >= 30%+)."},
            "C2_conservation": {"result": "PASS", "score": 0.94, "reason": "Quantitative commitment preserved; no replacement."},
            "C3_boundedScope": {"result": "PASS", "reason": "No unbounded claim ('best', 'guaranteed') introduced."},
        },
        "claimResult": "ADMISSIBLE",
    }]
    method = {
        "model": "claude-sonnet-4-6",
        "promptTemplateHash": "sha256:" + sha256_text("GATE_PROMPT_v0.1 placeholder"),
        "temperature": 0,
        "embeddingModel": "text-embedding-3-large",
        "tolerances": {"entailment": 0.85, "embeddingDistance": 0.25},
    }

    cert = build_certificate(
        asset_text=ASSET, asset_id="ad-2026-0001", media_type="text/plain",
        sources=sources, method=method, claims=claims,
        license_terms={"terms": "paid social; US; through 2026-12-31; no derivative claims"},
        approver="0xAGENCYWALLET000000000000000000000000bEEF",
    )
    finalize(cert)

    print("cert hash:        ", cert["header"]["integrity"]["sha256"])
    print("integrity ok:     ", verify_integrity(cert))

    rep = verify_against_inputs(cert, ASSET.encode("utf-8"), {"case-study-q1-2026.md": SOURCE.encode("utf-8")})
    print("inputs match:     ", rep["ok"])

    # Tamper: silently widen the claim verdict after signing.
    cert["findings"]["claims"][0]["conditions"]["C2_conservation"]["result"] = "FAIL"
    print("integrity (tamper):", verify_integrity(cert), "(False = tamper detected)")

    # Re-finalize would re-bless it -- but then the on-chain ipMetadataHash no longer matches,
    # so the consumer's chain read catches it even if the file looks internally consistent.
    finalize(cert)
    wrong_asset = "Our clients see an up to 50% reduction in customer acquisition cost.\n"
    rep2 = verify_against_inputs(cert, wrong_asset.encode("utf-8"), {"case-study-q1-2026.md": SOURCE.encode("utf-8")})
    print("swapped asset ok: ", rep2["ok"], "(False = altered asset detected)")

    with open("sample_certificate.json", "w", encoding="utf-8") as f:
        json.dump(cert, f, indent=2, ensure_ascii=False)
    print("wrote sample_certificate.json")
