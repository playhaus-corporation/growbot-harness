# Layer 3 (anchor): assert the cert's canonical hash + IPFS CID are exactly what
# the CoreMetadataModule recorded on-chain, by reading the MetadataURISet event
# directly instead of scanning log strings. full_verify needs NO changes — the
# report keeps every key it returned before and adds a few diagnostics.

from __future__ import annotations

import certificate as cert_mod

# --- CoreMetadataModule event (protocol-core-v1) ----------------------------
#   MetadataURISet(address indexed ipId, string metadataURI, bytes32 metadataHash)
#   NFTTokenURISet(address indexed ipId, string nftTokenURI, bytes32 nftMetadataHash)
TOPIC_METADATA_URI_SET  = "0x3b2d707542587feff5c7fe05482776be67fd747e64c2545089b52f395d47de76"
TOPIC_NFT_TOKEN_URI_SET = "0x4f10167731fb167d83b433ff4ef88092caafdc25681547a8045f4c299a5245c5"


# --- helpers ----------------------------------------------------------------
def _norm(h: str) -> str:
    return (h or "").lower().removeprefix("0x")

def _strings_in(obj) -> list[str]:
    """Recursively collect every string in a JSON-ish structure (fallback path)."""
    out: list[str] = []
    if isinstance(obj, dict):
        for v in obj.values():
            out += _strings_in(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _strings_in(v)
    elif isinstance(obj, str):
        out.append(obj)
    return out

def _log_items(logs) -> list[dict]:
    """Blockscout v2 returns {'items':[...]}; tolerate a bare list too."""
    if isinstance(logs, dict):
        return logs.get("items", []) or []
    if isinstance(logs, list):
        return logs
    return []

def _decode_metadata_uri_set(item: dict) -> dict | None:
    """
    Return {'ipId','metadataURI','metadataHash'} for a MetadataURISet log,
    or None if this log isn't that event / can't be decoded.

    Handles both shapes Storyscan can return:
      (a) decoded  -> item['decoded']['parameters'] = [{name,value,...}, ...]
      (b) raw      -> item['topics'][0]==topic0, ABI-decode item['data']
    """
    # (a) decoded by the explorer (contract verified)
    decoded = item.get("decoded")
    if isinstance(decoded, dict):
        sig = (decoded.get("method_call") or decoded.get("call_type") or "")
        params = decoded.get("parameters") or []
        named = {p.get("name"): p.get("value") for p in params if isinstance(p, dict)}
        looks_right = sig.startswith("MetadataURISet") or (
            "metadataURI" in named and "metadataHash" in named)
        if looks_right:
            return {
                "ipId": str(named.get("ipId", "")),
                "metadataURI": str(named.get("metadataURI", "")),
                "metadataHash": str(named.get("metadataHash", "")),
                "_source": "decoded",
            }

    # (b) raw topics + data — match topic0 (a constant) and ABI-decode
    topics = item.get("topics") or []
    if topics and _norm(topics[0]) == _norm(TOPIC_METADATA_URI_SET):
        ip_id = ""
        if len(topics) > 1 and topics[1]:
            ip_id = "0x" + _norm(topics[1])[-40:]      # indexed address
        data = _norm(item.get("data", ""))
        try:
            words = [data[i:i+64] for i in range(0, len(data), 64)]
            # layout: word0 = offset to string, word1 = bytes32 metadataHash,
            #         then at <offset>: [len][bytes...]
            metadata_hash = "0x" + words[1]
            str_off = int(words[0], 16) // 32
            str_len = int(words[str_off], 16)
            raw = "".join(words[str_off + 1:])
            uri = bytes.fromhex(raw[:str_len * 2]).decode("utf-8", "replace")
            return {"ipId": ip_id, "metadataURI": uri,
                    "metadataHash": metadata_hash, "_source": "raw"}
        except (ValueError, IndexError):
            return None
    return None


# --- Layer 3 ----------------------------------------------------------------
def verify_anchor(cert: dict, client=None) -> dict:
    """
    Confirm the cert's canonical hash + IPFS CID are exactly what the
    CoreMetadataModule recorded on-chain in the mint tx. Reads the
    MetadataURISet event directly (decoded or raw); never raises on mismatch.
    """
    if client is None:
        from blockscout import Blockscout
        client = Blockscout()
    report = {"hasAnchor": False, "txFound": False, "txSuccess": False,
              "eventFound": False, "hashOnChain": False, "cidOnChain": False,
              "ipIdMatch": False, "ok": False, "decodePath": None, "notes": []}

    anchor = cert.get("anchor")
    if not anchor:
        report["notes"].append("certificate has no anchor block (not yet minted)")
        return report
    report["hasAnchor"] = True

    cert_hash = _norm(cert["header"]["integrity"]["sha256"])
    bound_hash = _norm(anchor.get("ipMetadataHash", ""))
    cid = (anchor.get("ipMetadataCid", "") or "").strip()
    tx_hash = anchor.get("txHash", "")
    ip_id = _norm(anchor.get("ipId", ""))

    # internal consistency: the anchor claims ipMetadataHash == cert hash
    if bound_hash != cert_hash:
        report["notes"].append("anchor.ipMetadataHash != certificate hash")

    tx = client.transaction(tx_hash)
    if "_error" in tx:
        report["notes"].append(f"tx fetch failed: {tx['_error']}")
        return report
    report["txFound"] = True
    report["txSuccess"] = tx.get("status") == "ok" or tx.get("result") == "success"

    logs = client.transaction_logs(tx_hash)
    if isinstance(logs, dict) and "_error" in logs:
        report["notes"].append(f"logs fetch failed: {logs['_error']}")
        return report

    # find the CoreMetadataModule MetadataURISet event and read it directly
    evt = next((d for d in (_decode_metadata_uri_set(it) for it in _log_items(logs)) if d), None)

    if evt:
        report["eventFound"] = True
        report["decodePath"] = evt["_source"]
        ev_hash = _norm(evt["metadataHash"])
        ev_uri = evt["metadataURI"]
        report["hashOnChain"] = (ev_hash == cert_hash)
        report["cidOnChain"] = bool(cid) and (cid in ev_uri)
        if ip_id:
            report["ipIdMatch"] = (_norm(evt["ipId"]) == ip_id) if evt["ipId"] else False
        if not report["hashOnChain"]:
            report["notes"].append(
                f"MetadataURISet.metadataHash ({ev_hash[:12]}…) != cert hash ({cert_hash[:12]}…)")
        if not report["cidOnChain"]:
            report["notes"].append("pinned CID not present in MetadataURISet.metadataURI")
    else:
        # fallback: no decodable MetadataURISet — scan strings (old behaviour)
        report["decodePath"] = "string-scan-fallback"
        report["notes"].append("no MetadataURISet event decoded; fell back to log string scan")
        hay = [s.lower() for s in _strings_in(logs)]
        report["hashOnChain"] = any(cert_hash in _norm(s) for s in hay)
        report["cidOnChain"] = bool(cid) and any(cid.lower() in s for s in hay)
        if not report["hashOnChain"]:
            report["notes"].append("certificate hash not found in mint tx logs")

    report["ok"] = (report["txFound"] and report["txSuccess"]
                    and report["eventFound"] and report["hashOnChain"]
                    and report["cidOnChain"])
    return report


def full_verify(cert: dict, ad_bytes: bytes, sources: dict[str, bytes],
                client=None) -> dict:
    """Run integrity, input, and live anchor verification."""
    inputs = cert_mod.verify_against_inputs(cert, ad_bytes, sources)
    anchor = verify_anchor(cert, client)
    return {
        "integrity": inputs["integrityOk"],
        "inputs": inputs["ok"],
        "anchor": anchor["ok"],
        "verified": inputs["ok"] and anchor["ok"],
        "detail": {"inputs": inputs, "anchor": anchor},
    }