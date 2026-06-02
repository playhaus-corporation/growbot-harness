"""
app.py · growbot verify page (demo)
===================================
Paste an asset + its sources, load its certificate, and re-test all three
verification layers against the record. Run with:

    streamlit run app.py

"Test, don't trust": integrity + inputs are local stdlib recomputes; the
anchor is a public Storyscan read. No layer routes through the agency.
"""
import json
import streamlit as st
import certificate
import verify

STORYSCAN_TX = "https://aeneid.storyscan.xyz/tx/"
# (key, label, blurb, detail_key) — integrity rides inside the inputs report
# (full_verify exposes it as inputs["integrityOk"]), so it has no own detail block.
LAYERS = [
    ("integrity", "Integrity", "certificate hash recomputes from its own contents", None),
    ("inputs",    "Inputs",    "pasted asset + sources hash to what the cert recorded", "inputs"),
    ("anchor",    "Anchor",    "cert hash + CID are what CoreMetadataModule put on-chain", "anchor"),
]


def passed(result: dict, layer: str) -> bool:
    """Layer pass/fail, tolerant of where full_verify exposes it."""
    if isinstance(result.get(layer), bool):
        return result[layer]
    return bool(result.get("detail", {}).get(layer, {}).get("ok"))


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
st.title("growbot — verify a certified asset")
st.caption("Test, don't trust. Integrity and inputs are recomputed locally; "
           "the anchor is read from a public explorer. None of it trusts the agency.")

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
    tx_url = anchor.get("explorerUrl") or (STORYSCAN_TX + anchor["txHash"] if anchor.get("txHash") else None)
    if tx_url:
        st.divider()
        st.markdown(f"🔗 [View the mint transaction on Storyscan]({tx_url})")