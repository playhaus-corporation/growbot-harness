import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3  # pyright: ignore[reportMissingImports]

from story_sdk_compat import apply_story_sdk_compat

apply_story_sdk_compat()

from story_protocol_python_sdk import StoryClient  # noqa: E402  # pyright: ignore[reportMissingImports]
import certificate as cert_mod  # noqa: E402

load_dotenv()


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_client():
    w3 = Web3(Web3.HTTPProvider(os.environ["STORY_RPC"]))
    acct = w3.eth.account.from_key(os.environ["STORY_PRIVATE_KEY"])
    return StoryClient(w3, acct, chain_id=int(os.environ["STORY_CHAIN_ID"]))


def register(cert, ip_cid, nft_cid, nft_hash, pil_terms):
    client = get_client()
    resp = client.IPAsset.mint_and_register_ip_asset_with_pil_terms(
        spg_nft_contract=os.environ["NFT_CONTRACT"],
        terms=[pil_terms],
        ip_metadata={
            "ip_metadata_uri": ip_cid,
            "ip_metadata_hash": "0x" + cert["header"]["integrity"]["sha256"],
            "nft_metadata_uri": nft_cid,
            "nft_metadata_hash": nft_hash,
        },
    )
    cert_mod.attach_anchor(
        cert, chain_id=int(os.environ["STORY_CHAIN_ID"]),
        ip_id=resp["ip_id"], nft_contract=os.environ["NFT_CONTRACT"],
        token_id=str(resp["token_id"]), license_terms_id=str(resp.get("license_terms_ids", [""])[0]),
        ip_metadata_cid=ip_cid, tx_hash=resp["tx_hash"],
    )
    return cert, resp


def _explorer_url(tx_hash):
    base = os.environ.get("STORY_EXPLORER", "https://aeneid.storyscan.xyz").rstrip("/")
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    return f"{base}/tx/{tx_hash}"


def main():
    parser = argparse.ArgumentParser(
        description="Mint/register a growbot certificate on Story and print the tx id."
    )
    parser.add_argument("--cert", required=True, help="Finalized certificate JSON to anchor")
    parser.add_argument(
        "--registration",
        help="Story registration JSON; used for CIDs, nft hash, and PIL terms",
    )
    parser.add_argument("--ip-cid", help="IPFS URI/CID for the certificate metadata")
    parser.add_argument("--nft-cid", help="IPFS URI/CID for the NFT metadata")
    parser.add_argument("--nft-hash", help="0x-prefixed SHA-256 hash of NFT metadata")
    parser.add_argument("--pil-terms", help="JSON file containing one PIL terms object")
    parser.add_argument(
        "--out",
        help="Where to write the anchored certificate JSON (defaults to overwriting --cert)",
    )
    args = parser.parse_args()

    cert = load_json(args.cert)
    reg = load_json(args.registration) if args.registration else {}
    reg_args = reg.get("args", {})
    reg_metadata = reg_args.get("ip_metadata", {})

    ip_cid = args.ip_cid or reg_metadata.get("ip_metadata_uri")
    nft_cid = args.nft_cid or reg_metadata.get("nft_metadata_uri")
    nft_hash = args.nft_hash or reg_metadata.get("nft_metadata_hash")

    if args.pil_terms:
        pil_terms = load_json(args.pil_terms)
    else:
        terms = reg_args.get("terms", [])
        pil_terms = terms[0] if terms else None

    missing = [
        name
        for name, value in (
            ("ip_cid", ip_cid),
            ("nft_cid", nft_cid),
            ("nft_hash", nft_hash),
            ("pil_terms", pil_terms),
        )
        if not value
    ]
    if missing:
        parser.error(
            "missing required mint inputs: "
            + ", ".join(missing)
            + ". Provide them directly or via --registration."
        )

    anchored_cert, resp = register(cert, ip_cid, nft_cid, nft_hash, pil_terms)
    tx_hash = resp["tx_hash"]
    tx_id = tx_hash

    out_path = Path(args.out or args.cert)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(anchored_cert, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print("mint/register complete")
    print(f"tx_id: {tx_id}")
    print(f"tx_hash: {tx_hash}")
    print(f"explorer: {_explorer_url(tx_hash)}")
    print(f"ip_id: {resp['ip_id']}")
    print(f"token_id: {resp['token_id']}")
    print(f"anchored_cert: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())