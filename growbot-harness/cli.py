#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import certificate
import gate
import pinata
import register as story_register

ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=ENV_PATH)
DEFAULT_SOURCE_PATH = Path(__file__).parent / "samples" / "source_case_study_q1_2026.txt"

DEFAULT_LICENSE_TEXT = f"paid social; US; through {date.today().year + 1}-{date.today().month}-{date.today().day}; no derivative claims"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Story Aeneid (chainId 1315) protocol addresses. The defaults are the confirmed
# Aeneid deployment values; override via .env to retarget without a code change.
# These are published by Story upstream (Story docs + the protocol-periphery-v1
# / protocol-core-v1 repos), not in this project.
#   ROYALTY_POLICY_LAP — RoyaltyPolicyLAP (Liquid Absolute Percentage) contract.
#   WIP_CURRENCY        — Wrapped IP (WIP) ERC-20, the PIL settlement currency.
ROYALTY_POLICY_LAP = os.environ.get(
    "ROYALTY_POLICY_LAP", "0xBe54FB168b3c982b7AaE60dB6CF75Bd8447b390E"
)
WIP_CURRENCY = os.environ.get(
    "WIP_CURRENCY", "0x1514000000000000000000000000000000000000"
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()


def _sample_source_span(ad_path: Path) -> str | None:
    sample_id = ad_path.stem
    for claim_id, _claim, source_span, _expected in gate.SAMPLES:
        if claim_id == sample_id:
            return source_span
    return None


def _load_source_span(ad_path: Path, source_path: Path | None) -> tuple[str, str]:
    if source_path:
        return source_path.name, _read_text(source_path)

    sample_span = _sample_source_span(ad_path)
    if sample_span:
        return DEFAULT_SOURCE_PATH.name, sample_span

    if DEFAULT_SOURCE_PATH.exists():
        return DEFAULT_SOURCE_PATH.name, _read_text(DEFAULT_SOURCE_PATH)

    raise FileNotFoundError(
        "No source supplied. Pass --source or use one of the bundled samples."
    )


def _first_failing_condition(claim: dict) -> tuple[str | None, dict | None]:
    for key, finding in claim.get("conditions", {}).items():
        if finding.get("result") in {"FAIL", "NEEDS_REVIEW"}:
            return key, finding
    return None, None


def _print_blocked(gate_result: dict) -> None:
    print("BLOCKED")
    for claim in gate_result["claims"]:
        if claim["claimResult"] == "ADMISSIBLE":
            continue
        condition_key, finding = _first_failing_condition(claim)
        print(f"claim: {claim['claimId']}")
        print(f"condition: {condition_key}")
        if finding is None:
            print("reason: claim was blocked without a failing condition")
            return
        print(f"reason: {finding['reason']}")
        return
    print("reason: gate returned BLOCKED without a failing condition")


def _method_fingerprint() -> dict:
    return {
        "model": gate.ClaudeJudge.DEFAULT_MODEL,
        "promptTemplateHash": "sha256:" + certificate.sha256_text(gate.ClaudeJudge.PROMPT),
        "temperature": 0,
        "embeddingModel": None,
        "tolerances": {"entailment": "judge-calibrated", "c2NumericTolerance": 0.08},
    }


def _approver_address() -> str:
    if os.environ.get("APPROVER_ADDRESS"):
        return os.environ["APPROVER_ADDRESS"]
    if os.environ.get("STORY_PRIVATE_KEY"):
        try:
            Web3 = __import__("web3", fromlist=["Web3"]).Web3
            return Web3().eth.account.from_key(os.environ["STORY_PRIVATE_KEY"]).address
        except Exception:
            pass
    return "0xDEMO00000000000000000000000000000000bEEF"


def _default_pil_terms(uri: str = "") -> dict:
    return {
        "terms": {
            "transferable": True,
            "royalty_policy": ROYALTY_POLICY_LAP,
            "default_minting_fee": 0,
            "expiration": 0,
            "commercial_use": True,
            "commercial_attribution": True,
            "commercializer_checker": ZERO_ADDRESS,
            "commercializer_checker_data": "0x",
            "commercial_rev_share": 0,
            "commercial_rev_ceiling": 0,
            "derivatives_allowed": False,
            "derivatives_attribution": False,
            "derivatives_approval": False,
            "derivatives_reciprocal": False,
            "derivative_rev_ceiling": 0,
            "currency": WIP_CURRENCY,
            "uri": uri,
        },
        "licensing_config": {
            "is_set": False,
            "minting_fee": 0,
            "hook_data": "",
            "licensing_hook": ZERO_ADDRESS,
            "commercial_rev_share": 0,
            "disabled": False,
            "expect_minimum_group_reward_share": 0,
            "expect_group_reward_pool": ZERO_ADDRESS,
        },
    }


def _load_pil_terms(path: Path | None) -> dict:
    if not path:
        return _default_pil_terms(os.environ.get("PIL_TERMS_URI", ""))

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "args" in data:
        terms = data.get("args", {}).get("terms", [])
        if terms:
            return terms[0]
    if "terms" in data and "licensing_config" in data:
        return data
    if "transferable" in data:
        return {**_default_pil_terms(), "terms": data}
    raise ValueError(f"Could not read PIL terms from {path}")


def _json_sha256(obj: dict) -> str:
    raw = json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return "0x" + certificate.sha256_bytes(raw)


def _add_display_metadata(cert: dict, *, ad_text: str, asset_id: str) -> dict:
    cert.update(
        {
            "title": f"growbot admissible ad claim: {asset_id}",
            "description": "AI-generated ad copy passed through the growbot admissibility gate.",
            "creators": [
                {
                    "name": "Playhaus",
                    "address": cert["approval"]["approver"],
                    "contributionPercent": 100,
                    "description": "Agency of record; growbot operator",
                }
            ],
            "mediaHash": "0x" + certificate.sha256_text_normalized(ad_text),
            "mediaType": "text/plain",
            "tags": ["advertising", "substantiated", "growbot", "paid-social"],
        }
    )
    return cert


def _build_nft_metadata(cert: dict) -> dict:
    return {
        "name": cert["title"],
        "description": cert["description"],
        "image": "",
        "attributes": [
            {"trait_type": "Gate verdict", "value": cert["verdict"]["result"]},
            {"trait_type": "Certificate hash", "value": cert["header"]["integrity"]["sha256"]},
            {"trait_type": "Asset hash", "value": cert["subject"]["sha256"]},
        ],
    }


def _explorer_url(tx_hash: str) -> str:
    base = os.environ.get("STORY_EXPLORER", "https://aeneid.storyscan.xyz").rstrip("/")
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    return f"{base}/tx/{tx_hash}"


def _ensure_live_env() -> None:
    if "NFT_CONTRACT" not in os.environ and os.environ.get("SPG_NFT_CONTRACT"):
        os.environ["NFT_CONTRACT"] = os.environ["SPG_NFT_CONTRACT"]

    missing = [
        name
        for name in (
            "PINATA_JWT",
            "STORY_RPC",
            "STORY_CHAIN_ID",
            "STORY_PRIVATE_KEY",
            "NFT_CONTRACT",
        )
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(
            "missing required live mint env vars: " + ", ".join(missing)
        )


def _write_json(path: Path, obj: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _anthropic_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"missing ANTHROPIC_API_KEY; add it to {ENV_PATH} or export it before running cli.py"
        )
    anthropic = __import__("anthropic")
    return anthropic.Anthropic(api_key=api_key)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the growbot gate, then mint/register admissible ad copy on Story."
    )
    parser.add_argument("ad", type=Path, help="Ad copy text file, e.g. samples/s1.txt")
    parser.add_argument("--source", type=Path, help="Source text file for the cited claim")
    parser.add_argument("--asset-id", help="Certificate asset id; defaults to the ad filename stem")
    parser.add_argument("--license-terms", default=DEFAULT_LICENSE_TEXT, help="Human-readable license terms for the certificate")
    parser.add_argument("--pil-terms", type=Path, help="JSON file containing one Story PIL terms object")
    parser.add_argument("--out", type=Path, help="Optional path for the final certificate JSON")
    parser.add_argument("--dry-run", action="store_true", help="Stop after cert/NFT metadata prep; do not pin or mint")
    args = parser.parse_args()

    ad_path = args.ad
    ad_text = _read_text(ad_path)
    source_name, source_span = _load_source_span(ad_path, args.source)
    asset_id = args.asset_id or ad_path.stem

    claims_input = [
        {"claimId": asset_id, "claimText": ad_text, "sourceSpan": source_span}
    ]
    gate_result = gate.run_gate(claims_input, judge=gate.ClaudeJudge(_anthropic_client()))

    if gate_result["verdict"]["result"] != "ADMISSIBLE":
        _print_blocked(gate_result)
        return 1

    cert = certificate.build_certificate(
        asset_text=ad_text,
        asset_id=asset_id,
        media_type="text/plain",
        sources=[
            {
                "name": source_name,
                "sha256": certificate.sha256_text_normalized(source_span),
                "uri": None,
            }
        ],
        method=_method_fingerprint(),
        claims=gate_result["claims"],
        license_terms={"terms": args.license_terms},
        approver=_approver_address(),
    )
    _add_display_metadata(cert, ad_text=ad_text, asset_id=asset_id)
    certificate.finalize(cert)

    nft_metadata = _build_nft_metadata(cert)
    nft_hash = _json_sha256(nft_metadata)

    print("ADMISSIBLE")
    print(f"certificate_hash: {cert['header']['integrity']['sha256']}")
    print(f"nft_metadata_hash: {nft_hash}")

    if args.dry_run:
        print("dry_run: prepared certificate and NFT metadata; skipped Pinata and Story registration")
        if args.out:
            _write_json(args.out, cert)
            print(f"certificate: {args.out}")
        return 0

    _ensure_live_env()
    cert_cid = pinata.pin_json(cert, f"{asset_id}-admissibility-certificate.json")
    nft_cid = pinata.pin_json(nft_metadata, f"{asset_id}-nft-metadata.json")
    pil_terms = _load_pil_terms(args.pil_terms)

    anchored_cert, resp = story_register.register(
        cert,
        cert_cid,
        nft_cid,
        nft_hash,
        pil_terms,
    )

    if args.out:
        _write_json(args.out, anchored_cert)

    tx_hash = resp["tx_hash"]
    print("mint/register complete")
    print(f"ipId: {resp['ip_id']}")
    print(f"explorer: {_explorer_url(tx_hash)}")
    print(f"tx_hash: {tx_hash}")
    print(f"token_id: {resp['token_id']}")
    if args.out:
        print(f"anchored_cert: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
