# SIAS — Nemotron Evidence Crew

**Nebius x NVIDIA Global AI Hackathon 2026** | Best Apps and Agents

SIAS (SimpliSains) is an existing agent-operated science media company. Its research-to-publishing workflow coordinates specialized agents for scientific research, editorial scripting, storyboarding, illustration, narration, quality control and distribution. This **new hackathon sidecar** is a scientific claim verification agent designed to reduce unsupported explanations before they reach the production stage.

## NVIDIA + Nebius implementation

A scientific claim, with source snippets and source IDs, is sent to NVIDIA Nemotron through **Nebius Token Factory** via its OpenAI-compatible chat-completion endpoint. The response is verified against an output contract: supported/contradicted/insufficient_evidence, confidence, cited source IDs, explanation and a safer suggested revision.

- Nano: **nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B** for frequent economical checks.
- Super: **nvidia/nemotron-3-super-120b-a12b** for optional advanced reasoning.
- Hosted by **Nebius Token Factory**, API base https://api.tokenfactory.nebius.com/v1.

The code never substitutes a different inference provider. Credentials must be supplied through the environment, not source files.

## Quickstart (Python 3.10+; standard library only)

Run these commands from this folder:

1. Set NEBIUS_API_KEY as a secure environment variable (do not paste it into GitHub).
2. Run: python3 evidence_crew.py --dry-run
3. Run: python3 evidence_crew.py --model nano --case sample_case.json --output results/nano.json
4. Run: python3 app.py
5. Open http://127.0.0.1:18978 and click Run live verification.

No external call takes place on --dry-run. A qualifying live result includes response_id, the NVIDIA model name, token usage and measured latency.

## Architecture

SIAS Research Agent -> Claim + evidence excerpts -> Nebius / NVIDIA Nemotron -> Structured JSON verdict -> Editorial QA feedback -> Existing SIAS workflow.

The implementation has an actual live inference path at POST /api/verify (fixed demo claim), plus GET /api/status and GET /api/case. All endpoints are bounded by DEMO_MAX_CALLS; default localhost binding prevents uncontrolled anonymous spending.

## Tests and scientific integrity

Run: python3 -m unittest discover -s tests -v

Unit tests validate input, output schema and a mocked transport response. Mock tests do not count as live Nebius inference. Evidence snippets in sample_case.json are short context, not a reproduced full research article.

For model comparisons, evaluate the same set of claim/source pairs on both variants and record format validity, expert-checked correctness, useful corrections, token usage and latency. Do not infer accuracy from model confidence. Do not claim cost savings without a measured baseline and actual Nebius token costs.

## Working SIAS production baseline

Published video: https://youtu.be/cS6SBdIW0KA. Original SIAS black-hole episode manifest: 12 research sources, eight scenes, 59.375s video, estimated production spend US$0.4283. A separate VPS UAT captured a real monitor showing 12 labeled roles, sequential handoffs and two parallel worker groups, operating on artifacts of the already-produced episode. The UAT replay is not a fresh video generation, nor does it itself demonstrate Nemotron calls.

**Status note:** Source code and offline tests are available now. Do not represent the Nebius integration as validated until a live Token Factory response is captured. A missing API key leads to a clear error, not fake success.

## Safety and privacy

No keys or .env files should be committed. The demo binds to loopback and implements a strict global live-call budget. Do not deploy it publicly without an additional access-control layer. This is assistance for scientific review, not an automatic factual guarantee.

## License

The files inside this hackathon module are MIT-licensed (see LICENSE). The license does not implicitly relicense other files elsewhere in the pre-existing repository.

## Benchmarked live results (20 October 2026)

The first **20 authenticated Nebius inference calls** (five fixed science claims on Nano and Super, two prompt iterations) produced measured token usage and latency. See [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) and run `python3 benchmark_runner.py` with your own Nebius API key. On second iteration, Super returned valid structured results in 5/5 test cases and Nano in 4/5; the difference reveals real-world schema reliability trade-offs, not a definitive scientific accuracy ranking. Rates and inference costs are explicitly estimated and are not compared as proof of end-to-end SIAS savings. The live web app shows real Nemotron verdicts and tokens.
