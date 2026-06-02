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
- `verify.py` combines integrity, input, and on-chain anchor checks.
- `cli.py` wires the gate, certificate, Pinata, and Story registration flow.
- `app.py` provides a Streamlit verifier demo.
- `samples/` contains small ad/source fixtures.
- `examples/` contains Story metadata and registration examples.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` only for flows that need external services. Keep `.env` private.

## Offline Checks

These checks do not mint, pin, or spend testnet funds.

```bash
python certificate.py
python gate.py
python cli.py samples/s1.txt --dry-run --out /tmp/growbot-cert.json
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
