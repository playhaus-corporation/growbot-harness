#!/usr/bin/env python3
"""
assert_hashes.py — pre-mint integrity gate for the growbot harness.

Enforces the one equality that makes the on-chain record verifiable:

    sha256(canonicalize(cert)) == cert.header.integrity.sha256
                               == registration.args.ip_metadata.ip_metadata_hash[2:]

Plus: nft_metadata_hash reproduces, and mediaHash == 0x+subject.sha256.

The cert's canonicalization is read from header.integrity.canon, so this gate
follows whatever rule the cert declares (e.g.
"json:sort_keys,utf8,compact;exclude=header.integrity.sha256,approval.signature,approval.signedAt,anchor").

Stdlib only (sha256 discipline — no keccak). Exits 1 on any failure so it can
gate a Makefile / CI step / the mint command.

Usage:
    python assert_hashes.py \
        --ip-metadata   story_ip_metadata.json \
        --nft-metadata  story_nft_metadata.json \
        --registration  story_registration.json
"""
import argparse
import copy
import hashlib
import json
import re
import sys

HEX32 = re.compile(r"^0x[0-9a-fA-F]{64}$")


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_canon(canon: str):
    """Parse a canon spec like
    'json:sort_keys,utf8,compact;exclude=a.b.c,d.e' into options + exclude paths.
    Falls back to sort_keys+compact+utf8 with no excludes if unparseable.
    """
    sort_keys, compact, utf8 = True, True, True
    excludes = []
    if canon:
        opts_part, _, excl_part = canon.partition(";")
        opts = opts_part.split(":", 1)[-1]
        flags = {o.strip() for o in opts.split(",") if o.strip()}
        if flags:
            sort_keys = "sort_keys" in flags
            compact = "compact" in flags
            utf8 = "utf8" in flags
        for chunk in excl_part.split(","):
            chunk = chunk.strip()
            if chunk.startswith("exclude="):
                chunk = chunk[len("exclude="):]
            if chunk:
                excludes.append(chunk)
    return {"sort_keys": sort_keys, "compact": compact, "utf8": utf8}, excludes


def drop_path(doc, dotted):
    """Remove a dotted key path (object keys only) from a nested dict, in place."""
    parts = dotted.split(".")
    node = doc
    for p in parts[:-1]:
        if not isinstance(node, dict) or p not in node:
            return
        node = node[p]
    if isinstance(node, dict):
        node.pop(parts[-1], None)


def canonical_sha256(doc, opts, excludes):
    d = copy.deepcopy(doc)
    for path in excludes:
        drop_path(d, path)
    seps = (",", ":") if opts["compact"] else (", ", ": ")
    s = json.dumps(
        d,
        sort_keys=opts["sort_keys"],
        separators=seps,
        ensure_ascii=not opts["utf8"],
    )
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def main():
    ap = argparse.ArgumentParser(description="growbot pre-mint hash gate")
    ap.add_argument("--ip-metadata", default="story_ip_metadata.json")
    ap.add_argument("--nft-metadata", default="story_nft_metadata.json")
    ap.add_argument("--registration", default="story_registration.json")
    args = ap.parse_args()

    cert = load(args.ip_metadata)
    nft = load(args.nft_metadata)
    reg = load(args.registration)

    integrity = cert.get("header", {}).get("integrity", {})
    declared = integrity.get("sha256", "")
    canon = integrity.get("canon", "")
    opts, excludes = parse_canon(canon)

    checks = []  # (name, ok, detail)

    # 1) cert canonical hash reproduces the declared integrity hash
    recomputed = canonical_sha256(cert, opts, excludes)
    checks.append((
        "cert canonical sha256 == header.integrity.sha256",
        recomputed == declared,
        f"declared={declared or '<missing>'}  recomputed={recomputed}",
    ))

    # 2) the registered ip_metadata_hash equals the declared cert hash
    reg_ip_hash = (
        reg.get("args", {}).get("ip_metadata", {}).get("ip_metadata_hash", "")
    )
    if not HEX32.match(reg_ip_hash):
        checks.append((
            "registration.ip_metadata_hash is a 0x bytes32",
            False,
            f"value={reg_ip_hash!r} (still a placeholder? fill after cert is finalized)",
        ))
    else:
        checks.append((
            "registration.ip_metadata_hash[2:] == header.integrity.sha256",
            reg_ip_hash[2:].lower() == declared.lower(),
            f"registered={reg_ip_hash[2:]}  cert={declared}",
        ))

    # 3) nft_metadata_hash reproduces under the same sha256 discipline
    nft_recomputed = "0x" + canonical_sha256(nft, opts, [])
    reg_nft_hash = (
        reg.get("args", {}).get("ip_metadata", {}).get("nft_metadata_hash", "")
    )
    if not HEX32.match(reg_nft_hash):
        checks.append((
            "registration.nft_metadata_hash is a 0x bytes32",
            False,
            f"value={reg_nft_hash!r} (placeholder?)",
        ))
    else:
        checks.append((
            "nft_metadata_hash reproduces from story_nft_metadata.json",
            nft_recomputed.lower() == reg_nft_hash.lower(),
            f"registered={reg_nft_hash}  recomputed={nft_recomputed}",
        ))

    # 4) mediaHash == 0x + subject.sha256 (raw asset bytes)
    subj = cert.get("subject", {}).get("sha256", "")
    media = cert.get("mediaHash", "")
    checks.append((
        "mediaHash == 0x + subject.sha256",
        media.lower() == ("0x" + subj).lower() and bool(subj),
        f"mediaHash={media}  subject.sha256={subj}",
    ))

    # report
    width = max(len(n) for n, _, _ in checks)
    all_ok = True
    print("growbot pre-mint hash gate")
    print(f"  canon rule: {canon or '<none declared — using sort_keys,compact,utf8>'}")
    print(f"  excludes:   {excludes or '<none>'}")
    print("-" * (width + 12))
    for name, ok, detail in checks:
        all_ok &= ok
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name.ljust(width)}")
        if not ok:
            print(f"         -> {detail}")
    print("-" * (width + 12))

    if all_ok:
        print("OK — all hashes consistent. Safe to mint.")
        return 0

    print("BLOCKED — fix the failures above before minting.")
    print("Common cause: finalize() ran before the IPA display fields "
          "(title, creators, mediaUrl, tags, ...) were added, so the stored "
          "hash covers a different field set than the pinned file. Run "
          "certificate.finalize() LAST, regenerate, and re-run this gate.")
    return 1


if __name__ == "__main__":
    sys.exit(main())