# SIAS Nemotron Evidence Crew — Live Nebius UAT Benchmark

**Run date:** 20 October 2026 | **Provider:** Nebius Token Factory | **Models:** NVIDIA Nemotron 3 Nano 30B-A3B and Nemotron 3 Super 120B-A12B

This benchmark uses **real authenticated runtime inference** via the Nebius Token Factory API on an isolated VPS UAT instance. We supplied five fixed educational science claims with compact source excerpts and manually assigned expected classifications. We ran each case once per model per prompt round.

The test texts and answer labels are curated fixtures, **not** a peer-reviewed dataset, independent expert adjudication, or a production quality score. The exact-time-conversion claim is especially sensitive to rubric interpretation, so use its result as diagnostic evidence, not definitive model error.

## Results

| Prompt round | Model | Valid structured output | Agreement with curated expected labels | Median latency for valid responses | Mean estimated inference cost per valid response |
|---|---|---|---|---|---|
| 1 | Nano 30B-A3B | 5/5 | 4/5 valid | 2.71 s | $0.0000815 |
| 1 | Super 120B-A12B | 3/5 | 2/3 valid | 4.09 s | $0.0003027 |
| 2 | Nano 30B-A3B | 4/5 | 3/4 valid | 3.21 s | $0.0001065 |
| 2 | Super 120B-A12B | 5/5 | 4/5 valid | 2.58 s | $0.0003893 |

Source for public prices: [Nebius official Nemotron inference page](https://nebius.com/services/token-factory/models/nvidia-nemotron-models-inference).

Prices used (USD per 1 million tokens):
- Nano: $0.06 input / $0.24 output.
- Super: $0.30 input / $0.90 output.

Costs use the actual prompt/completion tokens returned by the API at public rates; they are **estimates**, not statements from a billing invoice. Requests with invalid JSON were not included in the mean estimate because that result path did not preserve usage metadata; actual billable spend can be higher. We do not assert savings versus SIAS proprietary-model baseline without direct comparable measurements.

## What the iteration taught us

1. Strict schema validation caught missing or blank correction fields even when a model produced useful prose. This protects downstream agent contracts.
2. Prompt revision increased Super's structured-output success from 3/5 to 5/5 in this small sample, but Nano went from 5/5 to 4/5. Improvements are not uniform.
3. The default production routing should prefer smaller Nano for routine checks but independently validate JSON and evidence support; escalate or retry when the verifier fails.
4. Model confidence is self-reported and does not prove scientific accuracy; no expert accuracy score has been assigned.
5. The broader SIAS video production cost estimate of US$0.4283 and these sub-cent claim-check costs are **different cost bases** and must not be directly compared as end-to-end savings.

## Actual app UI

The standalone test app calls NVIDIA Nemotron through Nebius using a bounded number of API calls (no mock response). Its success path shows the model ID, cited evidence IDs, verdict, suggested revision, response ID, token usage and measured API latency. No production SIAS services or publishing schedules were modified.

## Reproduction

Run the included `benchmark_runner.py` after securely configuring `NEBIUS_API_KEY`; never commit the key or generated private inference traces.
