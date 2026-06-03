# growbot harness

Growbot is a Python demo that checks AI-generated ad claims against source data,
issues a hash-bound admissibility certificate for claims that pass, and can anchor
that certificate as Story Protocol IP metadata.

The verifier is designed around "test, don't trust":

- recompute the certificate integrity hash locally
- recompute asset and source hashes from raw inputs
- read the Story Aeneid testnet anchor through a public explorer API

This is an applied-layer admissibility check inspired by SC-AS verification
discipline. It is not canonical SC-AS, it is not an RCC, and admissibility is not
legal compliance.

## Project Layout

- `gate.py` runs the C1-C3 admissibility gate over ad claims.
- `conditions.py` contains deterministic checks used by the gate.
- `certificate.py` builds and verifies canonical certificate hashes.
- `cert_verify.py` combines integrity, input, and on-chain anchor checks.
- `cli.py` wires the gate, certificate, Pinata, and Story registration flow.
- `app.py` provides a Streamlit verifier demo.
- `samples/` contains small ad/source fixtures.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` only for flows that need external services. Keep `.env` private.

## Offline Checks

These run with no keys and no network:

```bash
python certificate.py                      # cert hashing self-test
python gate.py                             # legacy C1-C3 self-test
python -m pytest tests/test_deterministic.py -q   # deterministic verifier acceptance suite
python cli.py --offline --dry-run --sample s3     # the hero refusal, end to end
```

## Deterministic quantitative verifier (`verify/`)

The mint gate is a **pure function over extracted numbers**: the LLM (in
`verify/extract.py`) does extraction *only* and never returns a verdict; the
verdict comes solely from `verify/deterministic.py`. Same inputs → same verdict,
with no LLM, clock, RNG, or network in the verify layer, so anyone holding only the
standard library can recompute it.

### Three outcomes (not two)

- **ADMISSIBLE** — the source deterministically licenses the claim. Eligible to mint.
- **INADMISSIBLE** — the claim is more aggressive than the source supports. Mint
  refused, with the arithmetic reason (e.g. `50 > 30 (HIGHER_IS_BETTER) -> overstatement`).
- **NEEDS_REVIEW** — the checker *abstains*: qualitative claim, units can't be
  normalized, no source value, or the 3 extraction runs disagreed. Routed to a
  human; **never certified**. The system never seals what it cannot deterministically verify.

### Adding a metric

Metrics are versioned **config, not code** — nothing in the core hard-codes one.
Add an entry to [`verify/rules.json`](verify/rules.json) under `metrics`:

```json
"cost_per_lead_usd": {
  "polarity": "LOWER_IS_BETTER",        // which direction is an OVERSTATEMENT (cost: cheaper is stronger)
  "unit": "$/lead",                      // canonical unit; the source is normalized onto it
  "precision": 0,                        // least-significant digit -> asymmetric rounding_tol = 0.5*10^-precision
  "aliases": ["per lead", "cost per lead", "/lead"],   // how extraction maps text to this metric
  "periodConversions": {"$/month": 12}   // allowed Rule C period conversions onto the canonical unit
}
```

Bump `ruleVersion` when you change tolerances/policy — it is recorded per claim in
the certificate, so a re-run on a different version is a different, self-describing check.

### Flags

- `--offline` — use the deterministic `LexicalJudge` instead of Claude, and skip the
  chain. No API key, no network; this is what the test suite and `pytest` exercise.
- `--dry-run` — run the full pipeline (real Claude extraction) and assemble the
  certificate + NFT metadata, but do not pin to IPFS or sign any Story transaction.
- `--sample s1..s6` — run a built-in fixture from `verify/samples.py` instead of files.

```bash
python cli.py --offline --dry-run --sample s3            # deterministic, no key
python cli.py samples/s1.txt --dry-run --out /tmp/c.json # real Claude extraction, no mint
```

## Streamlit Demo

```bash
streamlit run app.py
```

Upload a certificate JSON, paste the certified asset text, and upload the source
files referenced by the certificate.

## Live Story Flow

Live registration requires `PINATA_JWT`, Story Aeneid RPC settings, a funded
testnet private key, and an NFT contract address. The scripts that sign
transactions are:

- `setup_collection.py`
- `hello_world_mint.py`
- `register.py`
- `cli.py` without `--dry-run`

Review all environment values before running live commands.
