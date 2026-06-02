"""
app.py · growbot verify page (demo)
===================================
Paste an asset + its sources, load its certificate, and re-test all three
verification layers against the record. Run with:

    streamlit run app.py

"Test, don't trust": integrity + inputs are local stdlib recomputes; the
anchor is a public Storyscan read. No layer routes through the agency.
"""
from pathlib import Path
import json
import os

from dotenv import load_dotenv
import streamlit as st
import certificate
import gate
import verify

ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=ENV_PATH)
STORYSCAN_TX = "https://aeneid.storyscan.xyz/tx/"
# (key, label, blurb, detail_key) — integrity rides inside the inputs report
# (full_verify exposes it as inputs["integrityOk"]), so it has no own detail block.
LAYERS = [
    ("integrity", "Integrity", "certificate hash recomputes from its own contents", None),
    ("inputs",    "Inputs",    "pasted asset + sources hash to what the cert recorded", "inputs"),
    ("anchor",    "Anchor",    "cert hash + CID are what CoreMetadataModule put on-chain", "anchor"),
]


def anthropic_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"missing ANTHROPIC_API_KEY; add it to {ENV_PATH} or export it before running Streamlit"
        )
    anthropic = __import__("anthropic")
    return anthropic.Anthropic(api_key=api_key)


def run_claude_gate(claim_id: str, claim_text: str, source_span: str) -> tuple[dict, object]:
    judge = gate.ClaudeJudge(anthropic_client())
    result = gate.run_gate(
        [{"claimId": claim_id, "claimText": claim_text, "sourceSpan": source_span}],
        judge=judge,
    )
    return result, judge.last_usage


def usage_text(usage: object) -> str | None:
    if usage is None:
        return None
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if input_tokens is None and output_tokens is None:
        return str(usage)
    return f"input tokens: {input_tokens or 0}; output tokens: {output_tokens or 0}"


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


st.set_page_config(page_title="growbot · verify", page_icon="✅")
st.title("growbot — gate and verify")
st.caption("Test, don't trust. Integrity and inputs are recomputed locally; "
           "the anchor is read from a public explorer. None of it trusts the agency.")

gate_tab, verify_tab = st.tabs(["Run Gate", "Verify Certificate"])

with gate_tab:
    sample_options = {"custom": ("custom", "", "")}
    sample_options.update({claim_id: (claim_id, claim, source) for claim_id, claim, source, _expected in gate.SAMPLES})
    sample_id = st.selectbox("Bundled sample", list(sample_options), index=list(sample_options).index("s3"))
    default_id, default_claim, default_source = sample_options[sample_id]
    claim_id = st.text_input("Claim ID", value=default_id)
    claim_text = st.text_area("Claim", value=default_claim, height=100)
    source_span = st.text_area("Source span", value=default_source, height=120)

    if st.button("Run Gate with Claude", type="primary", disabled=not (claim_text.strip() and source_span.strip())):
        try:
            gate_result, usage = run_claude_gate(claim_id, claim_text, source_span)
        except Exception as exc:
            st.error(f"Claude gate failed: {exc}")
            st.stop()

        if gate_result["verdict"]["result"] == "ADMISSIBLE":
            st.success("ADMISSIBLE")
        else:
            st.error("BLOCKED")

        usage_summary = usage_text(usage)
        if usage_summary:
            st.caption("Claude usage — " + usage_summary)

        for claim in gate_result["claims"]:
            st.subheader(claim["claimId"])
            st.write(f"Claim result: {claim['claimResult']}")
            for condition_key, finding in claim["conditions"].items():
                label = f"{condition_key}: {finding['result']}"
                with st.expander(label, expanded=condition_key == "C1_sourceGrounding"):
                    if "score" in finding:
                        st.write(f"Score: {finding['score']}")
                    st.write(finding["reason"])

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
        result = verify.full_verify(cert, asset_text.encode(), sources)
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