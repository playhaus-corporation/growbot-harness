#!/usr/bin/env python3
"""
cli.py · the single growbot flow
================================

    extract (N=3)  ->  agreement gate  ->  for each claim: normalize -> check_claim
      ->  assemble certificate  ->  (only if every claim ADMISSIBLE) pin + mint

The verdict comes solely from verify/ (a pure function over extracted numbers). Any
INADMISSIBLE or NEEDS_REVIEW refuses the mint and prints the per-claim reason. The
LLM is confined to extraction (verify.extract); it never returns a verdict.

Flags:
  --offline   use the deterministic LexicalJudge and skip the chain (no key needed)
  --dry-run   run everything, assemble the cert, but do not pin or mint
  --sample s3 run a built-in fixture from verify.samples instead of an ad/source file
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import certificate
import pinata
import register as story_register
from verify import samples as verify_samples
from verify.config import Config
from verify.extract import ClaudeJudge as ExtractionJudge
from verify.extract import LexicalJudge
from verify.run import verify_claim

ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=ENV_PATH)
DEFAULT_SOURCE_PATH = Path(__file__).parent / "samples" / "source_case_study_q1_2026.txt"

DEFAULT_LICENSE_TEXT = f"paid social; US; through {date.today().year + 1}-{date.today().month}-{date.today().day}; no derivative claims"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Story Aeneid (chainId 1315) protocol addresses. The defaults are the confirmed
# Aeneid deployment values; override via .env to retarget without a code change.
ROYALTY_POLICY_LAP = os.environ.get(
    "ROYALTY_POLICY_LAP", "0xBe54FB168b3c982b7AaE60dB6CF75Bd8447b390E"
)
WIP_CURRENCY = os.environ.get(
    "WIP_CURRENCY", "0x1514000000000000000000000000000000000000"
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()


def _load_claim_and_source(args) -> tuple[str, str, str, str]:
    """Return (asset_id, claim_text, source_name, source_text) from --sample or files."""
    if args.sample:
        claim_text, source_text, _expected, _rule = verify_samples.sample(args.sample)
        return args.sample, claim_text, f"sample:{args.sample}", source_text

    if args.ad is None:
        raise SystemExit("provide an ad text file, or use --sample <id>")
    claim_text = _read_text(args.ad)
    asset_id = args.asset_id or args.ad.stem
    if args.source:
        return asset_id, claim_text, args.source.name, _read_text(args.source)
    if DEFAULT_SOURCE_PATH.exists():
        return asset_id, claim_text, DEFAULT_SOURCE_PATH.name, _read_text(DEFAULT_SOURCE_PATH)
    raise SystemExit("no source supplied; pass --source or use --sample")


def _print_refusal(asset_id: str, result: dict) -> None:
    print(result["result"])          # INADMISSIBLE or NEEDS_REVIEW
    print(f"claim: {asset_id}")
    print(f"rule: {result['rule_id']}")
    print(f"normalized_comparison: {result['normalized_comparison']}")
    print(f"reason: {result['reason']}")
    if result["result"] == "NEEDS_REVIEW":
        print("routing: human review (not certified)")


def _method_fingerprint(judge, config: Config) -> dict:
    """Records the extraction read + the rule version, so the verdict is reproducible."""
    prompt = getattr(judge, "PROMPT", "")
    return {
        "model": getattr(judge, "model", None) or "lexical-standin (offline)",
        "promptTemplateHash": ("sha256:" + certificate.sha256_text(prompt)) if prompt else "sha256:none",
        "temperature": getattr(judge, "temperature", 0),
        "embeddingModel": None,
        "tolerances": {
            "ruleVersion": config.rule_version,
            "policy": "asymmetric rounding; half least-significant-digit; conservative direction only",
        },
    }


def _cert_claim_entry(asset_id: str, claim_text: str, source_name: str, result: dict) -> dict:
    """The per-claim cert entry: §5 deterministic-verification fields + claimResult."""
    return {
        "claimId": asset_id,
        "claimText": claim_text,
        "sourceRef": {"name": source_name, "span": result["source_span"]},
        "metric": result["metric"],
        "polarity": result["polarity"],
        "claim_value": result["claim_value"],
        "claim_comparator": result["claim_comparator"],
        "unit": result["unit"],
        "extraction_agreement": result["extraction_agreement"],
        "source_value": result["source_value"],
        "source_kind": result["source_kind"],
        "source_span": result["source_span"],
        "normalized_comparison": result["normalized_comparison"],
        "rule_id": result["rule_id"],
        "ruleVersion": result["ruleVersion"],
        "result": result["result"],
        "reason": result["reason"],
        "claimResult": result["result"],   # build_certificate sums on this
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
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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
        for name in ("PINATA_JWT", "STORY_RPC", "STORY_CHAIN_ID", "STORY_PRIVATE_KEY", "NFT_CONTRACT")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError("missing required live mint env vars: " + ", ".join(missing))


def _write_json(path: Path, obj: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _anthropic_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"missing ANTHROPIC_API_KEY; add it to {ENV_PATH}, export it, or run with --offline"
        )
    anthropic = __import__("anthropic")
    return anthropic.Anthropic(api_key=api_key)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the growbot deterministic gate, then mint/register admissible ad copy on Story."
    )
    parser.add_argument("ad", type=Path, nargs="?", help="Ad copy text file, e.g. samples/s1.txt")
    parser.add_argument("--source", type=Path, help="Source text file for the cited claim")
    parser.add_argument("--sample", help="Run a built-in verify.samples fixture (s1..s6) instead of files")
    parser.add_argument("--asset-id", help="Certificate asset id; defaults to the ad filename stem")
    parser.add_argument("--license-terms", default=DEFAULT_LICENSE_TEXT, help="Human-readable license terms")
    parser.add_argument("--pil-terms", type=Path, help="JSON file containing one Story PIL terms object")
    parser.add_argument("--out", type=Path, help="Optional path for the final certificate JSON")
    parser.add_argument("--offline", action="store_true", help="Use the deterministic LexicalJudge; skip the chain")
    parser.add_argument("--dry-run", action="store_true", help="Assemble cert/NFT metadata; do not pin or mint")
    args = parser.parse_args()

    config = Config.load()
    asset_id, claim_text, source_name, source_text = _load_claim_and_source(args)

    judge = LexicalJudge(config) if args.offline else ExtractionJudge(_anthropic_client(), config)

    # extract (N=3) -> agreement gate -> normalize -> check_claim, all in verify_claim.
    result = verify_claim(claim_text, source_text, judge, config=config)

    if result["result"] != "ADMISSIBLE":
        _print_refusal(asset_id, result)
        return 1

    claim_entry = _cert_claim_entry(asset_id, claim_text, source_name, result)
    cert = certificate.build_certificate(
        asset_text=claim_text,
        asset_id=asset_id,
        media_type="text/plain",
        sources=[{
            "name": source_name,
            "sha256": certificate.sha256_text_normalized(source_text),
            "uri": None,
        }],
        method=_method_fingerprint(judge, config),
        claims=[claim_entry],
        license_terms={"terms": args.license_terms},
        approver=_approver_address(),
    )
    _add_display_metadata(cert, ad_text=claim_text, asset_id=asset_id)
    certificate.finalize(cert)

    nft_metadata = _build_nft_metadata(cert)
    nft_hash = _json_sha256(nft_metadata)

    print("ADMISSIBLE")
    print(f"normalized_comparison: {result['normalized_comparison']}")
    print(f"certificate_hash: {cert['header']['integrity']['sha256']}")
    print(f"nft_metadata_hash: {nft_hash}")

    if args.offline or args.dry_run:
        why = "offline" if args.offline else "dry_run"
        print(f"{why}: prepared certificate and NFT metadata; skipped Pinata and Story registration")
        # TODO(privacy): before pinning publicly, optionally emit a REDACTED cert here
        # (hashes + verdicts + normalized_comparison, source spans removed) and hand the
        # full cert to the asset-holder to verify locally against the same SHA-256 anchor.
        if args.out:
            _write_json(args.out, cert)
            print(f"certificate: {args.out}")
        return 0

    _ensure_live_env()
    # TODO(privacy): pin a redacted public cert instead of the full cert if source spans
    # are proprietary; the on-chain anchor is the SHA-256 of the cert either way.
    cert_cid = pinata.pin_json(cert, f"{asset_id}-admissibility-certificate.json")
    nft_cid = pinata.pin_json(nft_metadata, f"{asset_id}-nft-metadata.json")
    pil_terms = _load_pil_terms(args.pil_terms)

    anchored_cert, resp = story_register.register(cert, cert_cid, nft_cid, nft_hash, pil_terms)

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
