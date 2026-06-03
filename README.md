# Growbot Harness

Growbot checks advertising claims against their source data, issues a
hash-bound **admissibility certificate** for the claims that pass, and anchors
that certificate as owned, tamper-evident IP on Story Protocol. Input goes in;
a verifiable, ownable proof comes out.

## Both halves are load-bearing

Growbot is not an AI tool with a chain bolted on, nor a ledger with an AI
bolted on. Each half does work the other cannot, and removing either one strips
the system of its core utility:

- **Remove the AI and there is nothing to verify.** Extraction
  (`verify/extract.py`) turns unstructured ad copy and source documents into the
  normalized, comparable numbers the gate operates on. Without it the
  deterministic gate is an inert rules engine with no operands.
- **Remove the blockchain and there is nothing to trust.** Local recomputation
  proves a certificate is *correct given its inputs*; it cannot prove the
  certificate is *authentic, unaltered, first-in-time, or owned* — anyone can
  fabricate a self-consistent certificate offline. At mint, the certificate's
  `sha256` is committed on-chain as the `bytes32 metadataHash` of Story
  Protocol's `MetadataURISet` event, bound to a non-repudiable owner (`ipId`)
  and a block timestamp. `cert_verify.py` re-reads that event and asserts the
  on-chain hash equals the local certificate hash. Remove the anchor and the
  certificate degrades to a forgeable, unowned JSON file.

These are two separate guarantees, and they are deliberately inseparable:

| Guarantee | Established by | Answers |
|---|---|---|
| **Correctness** | offline deterministic verifier (`verify/deterministic.py`) | "Does the source license this claim?" |
| **Provenance** | on-chain `bytes32` commitment to the cert `sha256` (Story `MetadataURISet`), re-read and checked by `cert_verify.py` | "Is this certificate authentic, unaltered, owned, and first-in-time?" |

You need both to trust a claim. Recomputation without provenance is an
unsigned assertion; provenance without recomputation is a sealed black box.
Growbot refuses to be either — and it **never seals what it cannot
deterministically verify** (see the three-outcome design and adversarial trap
suite below).

## Track and frameworks

- **Intellectual Property** — the certificate is a tokenized, licensable IP
  asset with verifiable lineage: claim → source → rule version → owner,
  registered on Story Protocol.
- **Coherence discipline** — the gate is built around SC-AS *verification
  discipline*: deterministic checks, abstention over guessing, low-variance
  verdicts. This is an applied admissibility check, not canonical SC-AS.

### How to verify ("test, don't trust")

- recompute the certificate integrity hash locally (`integrity`)
- recompute asset and source hashes from raw inputs (`inputs`)
- re-read the Story Aeneid anchor and check the on-chain hash (`anchor`)

`cert_verify.full_verify` runs all three; the SC-AS scoping above is stated once and still holds.

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
python certificate.py                      # cert hashing + validity-window self-test
python gate.py                             # C1-C3 self-test
python -m pytest tests/ -q                 # full suite: deterministic + validity + traps
python -m pytest tests/test_traps.py -q    # adversarial soundness suite (see below)
python cli.py --offline --dry-run --sample s3     # deterministic refusal, end to end
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

- `--offline` — use the deterministic `LexicalJudge`, and skip the
  chain. No API key, no network; this is what the test suite and `pytest` exercise.
- `--dry-run` — run the full pipeline (model-backed extraction) and assemble the
  certificate + NFT metadata, but do not pin to IPFS or sign any Story transaction.
- `--sample s1..s6` — run a built-in fixture from `verify/samples.py` instead of files.

```bash
python cli.py --offline --dry-run --sample s3            # deterministic, no key
python cli.py samples/s1.txt --dry-run --out /tmp/c.json # model-backed extraction, no mint
```

### Adversarial trap suite (`tests/test_traps.py`)

The N=3 agreement gate catches **random** extraction error — three reads of ambiguous
text diverge, and the claim routes to NEEDS_REVIEW. It is blind to a **correlated**
misread: a confident, stable wrong read that agrees with itself every time. Those must
be caught structurally, in extraction + Rule C, or a false claim gets a cryptographic
seal.

`test_traps.py` drives deceptive `(claim, source)` pairs through the offline pipeline
with the deterministic `LexicalJudge` — itself a stable, confident reader, i.e. a
faithful stand-in for "the model misread it the same way three times." The cardinal
invariant:

> a deceptive pair is **never ADMISSIBLE** — INADMISSIBLE or NEEDS_REVIEW are both safe.

Certifying a false claim is the one failure this whole design exists to prevent;
refusing or abstaining is always acceptable. Controls (legitimate claims that must
still pass) keep the suite from trivially "refusing everything."

Deception families covered:

| trap | the lie it encodes |
|---|---|
| `pp_vs_pct` | "30%" vs "30 percentage points" — same digits, different quantity |
| `avg_as_ceiling_cost` | "as little as $2/lead" against a $3 **average** (cost polarity) |
| `cue_far_from_figure` | "up to" qualifies a different number than the headline |
| `population_mismatch` | "clients" silently reconciled to "customers" |
| `period_inflation` | "$5,000/yr" against "$120/month" (= $1,440/yr) after Rule C |
| `rounding_just_over` | a claim exceeding the source past the half-point tolerance |
| `no_source_for_metric` | a claim with no cited evidence for its metric |
| `guaranteed_floor_no_evidence` | a "30%+" floor claim against a mere average |
| `aspiration` | "up to 45%" backed only by "we **aim** as high as 60%" |

```bash
python -m pytest tests/test_traps.py -q
```

**Goal vs. achieved (the aspiration policy).** A source can mix a real result with a
goal — "averaged 30%, and we aim as high as 60%." The goal figure is not evidence.
`extract.demote_if_aspirational` re-scans the recorded `source_span` around the
strongest figure: a goal cue (`aim`, `target`, `projected`, `on track`, `designed to`,
`potential`, `could see`) with **no** achieved cue (`saw`, `achieved`, `accomplished`,
`succeeded`) relabels the source `ASPIRATIONAL`, and the core abstains (NEEDS_REVIEW).
Achievement overrides a co-located goal word, so "aimed high and **achieved** 60%"
still counts as evidence.

This guard runs in `verify_claim`, so it applies to **any** judge — the production
`ClaudeJudge` (also prompted to label goals `ASPIRATIONAL`) and the offline
`LexicalJudge` alike. It is a deterministic re-read of the span, never a trust in the
extractor's self-classification.

**Limitation (stated, not hidden).** The cue lists are lexical, so paraphrases they
don't contain ("shooting for 60%", "stretch goal of 60%") can still slip the goal
detector. The net under that is the three-outcome design: genuinely ambiguous reads
trend to NEEDS_REVIEW rather than ADMISSIBLE.

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
