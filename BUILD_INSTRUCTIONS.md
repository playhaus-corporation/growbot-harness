# BUILD_INSTRUCTIONS.md

Canonical runbook for `growbot-harness`.

This file is the execution guide for local development, validation, and demo prep.
It separates **no-signing** tasks from **signing** tasks.

---

## 1) Prerequisites

- Python 3.11+ recommended
- macOS/Linux shell commands below assume `zsh`/`bash`
- Network access for dependency install and any API-backed steps

---

## 2) Environment setup

From repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Sanity check imports:

```bash
python -c "import web3, story_protocol_python_sdk, dotenv; print('ok')"
```

---

## 3) Env var guide

`./.env` keys used by this project:

- Always useful:
  - `STORY_RPC` (default Aeneid RPC in `.env.example`)
  - `STORY_CHAIN_ID` (`1315` for Aeneid)
  - `STORYSCAN_API` (Aeneid Storyscan API base)
  - `STORY_EXPLORER` (Aeneid explorer base)
- Needed for Claude gate:
  - `ANTHROPIC_API_KEY`
- Needed for IPFS pinning:
  - `PINATA_JWT`
- Needed for any signing/minting:
  - `STORY_PRIVATE_KEY` (testnet burner key only)
  - `NFT_CONTRACT`
- Optional:
  - `APPROVER_ADDRESS` (otherwise derived from `STORY_PRIVATE_KEY` when available)
  - `ROYALTY_POLICY_LAP` (override; otherwise uses confirmed Aeneid default in `cli.py`)
  - `WIP_CURRENCY` (override; otherwise uses confirmed Aeneid default in `cli.py`)

---

## 4) Safe no-signing validation (recommended first)

These do not sign transactions and do not spend testnet funds.

```bash
source .venv/bin/activate
python certificate.py
python gate.py
```

Expected:
- `certificate.py`: integrity `True`, tamper detection `False`, swapped asset `False`
- `gate.py`: `ALL EXPECTED OUTCOMES MET: True`

---

## 5) Gate dry-run (no pin, no mint)

This runs the real Claude judge and writes a local certificate, but does **not** pin
to IPFS and does **not** sign on Story:

```bash
source .venv/bin/activate
python cli.py samples/s1.txt --dry-run --out /tmp/growbot-cert.json
```

Notes:
- Requires `ANTHROPIC_API_KEY`
- Uses network
- Safe from chain-spend/signing perspective

---

## 6) Streamlit demo

```bash
source .venv/bin/activate
streamlit run app.py
```

Tabs:
- **Run Gate**: evaluate claim + source; optional live mint toggle
- **Verify Certificate**: re-run integrity + input + anchor checks

---

## 7) Signing commands (manual approval checkpoint)

These commands sign transactions. Run only when you explicitly intend to mint/register.

### 7.1 One-time collection setup

```bash
source .venv/bin/activate
python setup_collection.py
```

Copy printed `NFT_CONTRACT = ...` into `.env`.

### 7.2 Hello-world mint probe

```bash
source .venv/bin/activate
python hello_world_mint.py
```

### 7.3 Full live flow via CLI

```bash
source .venv/bin/activate
python cli.py samples/s1.txt --out anchored_certificate.json
```

This performs:
1) gate pass,
2) certificate build/finalize,
3) Pinata pin (cert + NFT metadata),
4) Story mint/register,
5) anchor attachment into output cert.

### 7.4 Register from existing cert (advanced)

```bash
source .venv/bin/activate
python register.py \
  --cert /path/to/cert.json \
  --ip-cid ipfs://... \
  --nft-cid ipfs://... \
  --nft-hash 0x... \
  --pil-terms /path/to/pil_terms.json
```

---

## 8) Verify anchored certificates

Programmatic verification lives in `cert_verify.py` and `certificate.py`.

- Integrity: canonical cert hash recompute
- Inputs: asset/source hash recompute from user-held bytes
- Anchor: Storyscan/Blockscout tx + logs check

For interactive verification, use the Streamlit app (`app.py`).

---

## 9) Troubleshooting

- `missing ANTHROPIC_API_KEY`: set key in `.env` for Claude gate paths
- `missing required live mint env vars`: ensure `PINATA_JWT`, `STORY_RPC`, `STORY_CHAIN_ID`, `STORY_PRIVATE_KEY`, `NFT_CONTRACT`
- Story SDK import issues: ensure `story_sdk_compat.py` is imported before `StoryClient` usage (already done in scripts)
- Storyscan read failures: confirm `STORYSCAN_API` host and test with known tx
- Verification fails on anchor: confirm certificate has `anchor` block from successful mint/register output

---

## 10) Current repo status checklist

- [x] Offline self-tests wired (`certificate.py`, `gate.py`)
- [x] Claude judge integrated in `cli.py` and `app.py`
- [x] Pinata + register wiring in CLI
- [x] Streamlit gate + verify tabs
- [ ] Live mint path validated end-to-end on your funded Aeneid wallet (manual step)
- [ ] Post-live-mint anchor event/topic verification tightened if Story event shape differs
