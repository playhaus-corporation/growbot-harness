"""
app.py · growbot gate + verify page (demo)
==========================================
Two tabs:

  Run Gate          extract (N=3) -> agreement gate -> normalize -> check_claim, the
                    deterministic admissibility verdict. The LLM only extracts numbers;
                    the verdict is pure arithmetic (verify/). Build a cert and, if every
                    claim is ADMISSIBLE, optionally mint/register on Story.
  Verify Certificate  re-test a finished cert against reality: integrity + inputs are
                    local stdlib recomputes; the anchor is a public Storyscan read.

Run with:  streamlit run app.py

"Test, don't trust": no layer routes through the agency, and there is no LLM in the
judgment — the refusal you see on an overstatement is the arithmetic, on screen.
"""
from pathlib import Path
from datetime import date
import json
import os

from dotenv import load_dotenv
import streamlit as st

import certificate
import cert_verify
from verify import samples as verify_samples
from verify.config import Config
from verify.extract import ClaudeJudge as ExtractionJudge
from verify.extract import LexicalJudge
from verify.run import verify_claim

ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=ENV_PATH)
STORYSCAN_TX = "https://aeneid.storyscan.xyz/tx/"
DEFAULT_LICENSE_TEXT = f"paid social; US; through {date.today().year + 1}-{date.today().month}-{date.today().day}; no derivative claims"
CONFIG = Config.load()

# (key, label, blurb, detail_key) — integrity rides inside the inputs report
# (full_verify exposes it as inputs["integrityOk"]), so it has no own detail block.
LAYERS = [
    ("integrity", "Integrity", "certificate hash recomputes from its own contents", None),
    ("inputs",    "Inputs",    "pasted asset + sources hash to what the cert recorded", "inputs"),
    ("anchor",    "Anchor",    "cert hash + CID are what CoreMetadataModule put on-chain", "anchor"),
]

# §5 cert fields, in the order a reviewer eyeballs "right numbers?" in two seconds.
RECORD_FIELDS = [
    ("metric", "metric"), ("polarity", "polarity"),
    ("claim_value", "claim value"), ("claim_comparator", "comparator"), ("unit", "unit"),
    ("extraction_agreement", "extraction agreement"),
    ("source_value", "source value"), ("source_kind", "source kind"),
    ("rule_id", "rule"), ("ruleVersion", "rule version"),
]


def anthropic_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"missing ANTHROPIC_API_KEY; add it to {ENV_PATH}, export it, or use Offline mode"
        )
    anthropic = __import__("anthropic")
    return anthropic.Anthropic(api_key=api_key)


def make_judge(offline: bool):
    """LexicalJudge (deterministic, no key) offline; ClaudeJudge (extraction-only) live."""
    return LexicalJudge(CONFIG) if offline else ExtractionJudge(anthropic_client(), CONFIG)


def method_fingerprint(judge, config: Config) -> dict:
    """Records the extraction read + the rule version so the verdict is reproducible."""
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


def cert_claim_entry(asset_id: str, claim_text: str, source_name: str, result: dict) -> dict:
    """Per-claim cert entry: §5 deterministic-verification fields + claimResult."""
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


def approver_address() -> str:
    if os.environ.get("APPROVER_ADDRESS"):
        return os.environ["APPROVER_ADDRESS"]
    if os.environ.get("STORY_PRIVATE_KEY"):
        try:
            Web3 = __import__("web3", fromlist=["Web3"]).Web3
            return Web3().eth.account.from_key(os.environ["STORY_PRIVATE_KEY"]).address
        except Exception:
            pass
    return "0xDEMO00000000000000000000000000000000bEEF"


def add_display_metadata(cert: dict, *, ad_text: str, asset_id: str) -> dict:
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


def build_certificate_from_result(
    *, asset_id: str, claim_text: str, source_name: str, source_text: str,
    result: dict, judge,
) -> dict:
    cert = certificate.build_certificate(
        asset_text=claim_text,
        asset_id=asset_id,
        media_type="text/plain",
        sources=[{
            "name": source_name,
            "sha256": certificate.sha256_text_normalized(source_text),
            "uri": None,
        }],
        method=method_fingerprint(judge, CONFIG),
        claims=[cert_claim_entry(asset_id, claim_text, source_name, result)],
        license_terms={"terms": DEFAULT_LICENSE_TEXT},
        approver=approver_address(),
    )
    add_display_metadata(cert, ad_text=claim_text, asset_id=asset_id)
    certificate.finalize(cert)
    return cert


def cert_json(cert: dict) -> str:
    return json.dumps(cert, indent=2, ensure_ascii=False) + "\n"


def mint_certificate_on_story(cert: dict, *, asset_id: str) -> dict:
    import cli as cli_helpers
    import pinata
    import register as story_register

    cli_helpers._ensure_live_env()
    nft_metadata = cli_helpers._build_nft_metadata(cert)
    nft_hash = cli_helpers._json_sha256(nft_metadata)
    cert_cid = pinata.pin_json(cert, f"{asset_id}-admissibility-certificate.json")
    nft_cid = pinata.pin_json(nft_metadata, f"{asset_id}-nft-metadata.json")
    pil_terms = cli_helpers._load_pil_terms(None)
    anchored_cert, resp = story_register.register(cert, cert_cid, nft_cid, nft_hash, pil_terms)
    return {
        "cert": anchored_cert,
        "response": resp,
        "cert_cid": cert_cid,
        "nft_cid": nft_cid,
        "nft_hash": nft_hash,
    }


def usage_text(usage) -> str | None:
    if not usage:
        return None
    if isinstance(usage, dict):
        return (f"input tokens: {usage.get('input_tokens', 0)}; "
                f"output tokens: {usage.get('output_tokens', 0)} "
                f"across {usage.get('calls', 0)} extraction calls")
    return str(usage)


def passed(result: dict, layer: str) -> bool:
    """Layer pass/fail, tolerant of where full_verify exposes it."""
    if isinstance(result.get(layer), bool):
        return result[layer]
    return bool(result.get("detail", {}).get(layer, {}).get("ok"))


def storyscan_tx_url(anchor: dict) -> str | None:
    """Build a Storyscan transaction URL from either 0x-prefixed or bare tx hashes."""
    tx_hash = (anchor.get("txHash") or "").strip()
    if tx_hash:
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        return STORYSCAN_TX + tx_hash
    return anchor.get("explorerUrl")


def ipfs_gateway_url(uri: str) -> str | None:
    """Turn ipfs://CID[/path] into a browser-viewable gateway URL."""
    uri = (uri or "").strip()
    if not uri:
        return None
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri
    if not uri.startswith("ipfs://"):
        return None
    gateway = os.environ.get("PINATA_GATEWAY", "https://gateway.pinata.cloud/ipfs/").rstrip("/") + "/"
    return gateway + uri[len("ipfs://") :]


def certificate_sources(cert: dict, uploaded_files) -> tuple[dict[str, bytes], list[str]]:
    """Match uploaded source files to the source names recorded in the certificate."""
    uploaded = {f.name: f.getvalue() for f in (uploaded_files or [])}
    sources: dict[str, bytes] = {}
    used_uploads: set[str] = set()
    notes: list[str] = []

    by_hash: dict[str, list[tuple[str, bytes]]] = {}
    for name, data in uploaded.items():
        digest = certificate.sha256_declared_text_bytes(data)
        by_hash.setdefault(digest, []).append((name, data))

    for source in cert.get("sources", []):
        declared_name = source.get("name", "")
        expected_hash = source.get("sha256", "")
        if declared_name in uploaded:
            sources[declared_name] = uploaded[declared_name]
            used_uploads.add(declared_name)
            continue

        matches = by_hash.get(expected_hash, [])
        if len(matches) == 1:
            uploaded_name, data = matches[0]
            sources[declared_name] = data
            used_uploads.add(uploaded_name)
            notes.append(
                f"mapped uploaded source '{uploaded_name}' to certificate source "
                f"'{declared_name}' by SHA-256"
            )
        elif len(matches) > 1:
            notes.append(
                f"multiple uploaded files match certificate source '{declared_name}'; "
                "rename one to the certificate source name"
            )

    extras = sorted(set(uploaded) - used_uploads)
    for name in extras:
        if all(name != note_source.get("name") for note_source in cert.get("sources", [])):
            notes.append(f"ignored uploaded source '{name}' because it is not declared in the certificate")

    return sources, notes


def render_record(result: dict) -> None:
    """The human-legible extraction record + the arithmetic that produced the verdict."""
    st.code(result["normalized_comparison"], language=None)
    cols = st.columns(2)
    for i, (key, label) in enumerate(RECORD_FIELDS):
        cols[i % 2].markdown(f"**{label}:** `{result.get(key)}`")
    st.caption(f"source span — {result['source_span']}")
    st.caption(f"reason — {result['reason']}")


st.set_page_config(page_title="growbot · verify", page_icon="✅")
st.title("growbot — gate and verify")
st.caption("Test, don't trust. The verdict is pure arithmetic over extracted numbers — "
           "no LLM in the judgment — and verification never routes through the agency.")

gate_tab, verify_tab = st.tabs(["Run Gate", "Verify Certificate"])

with gate_tab:
    sample_options = {"custom": ("custom", "", "")}
    sample_options.update({sid: (sid, claim, source) for sid, (claim, source, _exp, _rule) in verify_samples.SAMPLES.items()})
    sample_id = st.selectbox("Bundled sample", list(sample_options), index=list(sample_options).index("s3"),
                             help="s3 is the hero: 'up to 50%' against a 30% average → INADMISSIBLE.")
    default_id, default_claim, default_source = sample_options[sample_id]
    claim_id = st.text_input("Claim ID", value=default_id)
    claim_text = st.text_area("Claim", value=default_claim, height=100)
    source_name = st.text_input("Source name", value="source.txt")
    source_span = st.text_area("Source / evidence text", value=default_source, height=120)

    offline = st.checkbox(
        "Offline (deterministic LexicalJudge — no API key, no network)",
        value=True,
        help="On: regex/parse extraction, fully reproducible. Off: Claude does the N=3 extraction (records only, never a verdict).",
    )
    mint_live = st.checkbox(
        "Mint/register on Story if admissible",
        value=False,
        help="Runs Pinata pinning and signs a Story Aeneid testnet transaction with STORY_PRIVATE_KEY.",
    )
    if mint_live:
        st.warning("Live mint is enabled. This will create a new Story testnet token if every claim is ADMISSIBLE.")

    run_label = "Run Gate (offline)" if offline else "Run Gate with Claude"
    if st.button(run_label, type="primary", disabled=not (claim_text.strip() and source_span.strip())):
        try:
            judge = make_judge(offline)
            result = verify_claim(claim_text, source_span, judge, config=CONFIG)
        except Exception as exc:
            st.error(f"Gate failed: {exc}")
            st.stop()

        verdict = result["result"]

        if verdict == "ADMISSIBLE":
            st.success("ADMISSIBLE — the source deterministically licenses this claim.")
        elif verdict == "INADMISSIBLE":
            st.error("INADMISSIBLE — the claim is more aggressive than the source supports. Mint refused.")
        else:
            st.warning("NEEDS_REVIEW — the checker abstains and routes to a human. Not certified, not minted.")

        st.subheader("Deterministic record")
        render_record(result)

        if verdict == "ADMISSIBLE":
            cert = build_certificate_from_result(
                asset_id=claim_id,
                claim_text=claim_text,
                source_name=source_name or "source.txt",
                source_text=source_span,
                result=result,
                judge=judge,
            )
            st.subheader("Certificate")
            st.write(f"Certificate hash: `{cert['header']['integrity']['sha256']}`")
            st.write(f"Approver wallet: `{cert['approval']['approver']}`")
            st.write(f"Asset hash: `{cert['subject']['sha256']}`")

            if mint_live:
                with st.spinner("Pinning metadata and minting/registering on Story Aeneid..."):
                    try:
                        minted = mint_certificate_on_story(cert, asset_id=claim_id)
                    except Exception as exc:
                        st.error(f"Story mint/register failed: {exc}")
                        st.stop()
                cert = minted["cert"]
                resp = minted["response"]
                tx_hash = resp.get("tx_hash", "")
                tx_url = storyscan_tx_url({"txHash": tx_hash})
                st.success("Mint/register complete")
                st.write(f"IP ID: `{resp.get('ip_id')}`")
                st.write(f"Token ID: `{resp.get('token_id')}`")
                st.write(f"Transaction ID: `{tx_hash}`")
                st.write(f"Certificate IPFS URI: `{minted['cert_cid']}`")
                st.write(f"NFT metadata IPFS URI: `{minted['nft_cid']}`")
                cert_gateway = ipfs_gateway_url(minted["cert_cid"])
                nft_gateway = ipfs_gateway_url(minted["nft_cid"])
                if cert_gateway:
                    st.markdown(f"🔗 [View certificate on IPFS gateway]({cert_gateway})")
                if nft_gateway:
                    st.markdown(f"🔗 [View NFT metadata on IPFS gateway]({nft_gateway})")
                if tx_url:
                    st.markdown(f"🔗 [View transaction on Storyscan]({tx_url})")
            else:
                st.info("Gate only: no token was minted. Enable live mint to pin metadata and register on Story.")

            st.download_button(
                "Download certificate JSON",
                data=cert_json(cert),
                file_name=f"{claim_id}-certificate.json",
                mime="application/json",
            )
        else:
            st.info("No certificate was created and no token was minted.")

        usage_summary = usage_text(getattr(judge, "last_usage", None))
        if usage_summary:
            st.caption("Claude usage — " + usage_summary)

with verify_tab:
    cert_file = st.file_uploader("Certificate (cert.json)", type="json")
    asset_text = st.text_area("Asset — the exact certified copy", height=150)
    source_files = st.file_uploader("Sources (the files the claims cite)", accept_multiple_files=True)

    if st.button("Verify", type="primary", disabled=not (cert_file and asset_text.strip())):
        if cert_file is None:
            st.error("Please upload a certificate before verifying.")
            st.stop()

        cert = json.load(cert_file)
        sources, source_notes = certificate_sources(cert, source_files)
        result = cert_verify.full_verify(cert, asset_text.encode(), sources)
        result.setdefault("detail", {}).setdefault("inputs", {}).setdefault("notes", []).extend(source_notes)

        if result.get("verified"):
            st.success("VERIFIED — all three layers pass.")
        else:
            st.error("NOT VERIFIED — at least one layer failed.")

        for key, label, blurb, detail_key in LAYERS:
            ok = passed(result, key)
            st.subheader(("✅ " if ok else "❌ ") + label)
            st.write(blurb)
            if detail_key:
                for note in result.get("detail", {}).get(detail_key, {}).get("notes", []) or []:
                    st.caption("· " + note)

        anchor = cert.get("anchor") or {}
        tx_url = storyscan_tx_url(anchor)
        if tx_url:
            st.divider()
            st.markdown(f"🔗 [View the mint transaction on Storyscan]({tx_url})")
