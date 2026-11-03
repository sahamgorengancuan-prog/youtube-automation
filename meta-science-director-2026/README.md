# SimpliSains Science Director

**Meta Global AI Developer Hackathon 2026 — Indie Developer Track / Build for Your World**

Science Director explores evidence-grounded short-form science storytelling using a **Research → Director → Critic → Repair** workflow. This is a standalone competition prototype and does **not** modify the SimpliSains production pipeline.

## What actually works

- **Offline web preview:** Python stdlib HTTP server, scene visualizations and citations for a published science question.
- **Workflow implementation:** `core.py` validates evidence identifiers, storyboard structures, critic outputs, revision limits, and fails closed on fabricated evidence IDs.
- **Optional Meta Model API adapter:** `meta_adapter.py` integrates with the documented OpenAI-compatible chat-completions protocol, using `muse-spark-1.3` and a user-provided `MODEL_API_KEY`. This path **has not been independently verified with a successful live response** as of November 3, 2026.
- **Automated regression tests:** 20 unit tests passed locally on Python 3.12.
- **Safety boundary:** The public web preview never loads API keys or makes paid inference calls.

## Quick start — no key required

```bash
git clone https://github.com/sahamgorengancuan-prog/youtube-automation.git
cd youtube-automation/meta-science-director-2026
python3 -m unittest discover -s tests -v
python3 showcase_server.py
# open http://127.0.0.1:8891/
```

Requires Python 3.10+. The offline demo has no external dependencies.

## Zero-generation authentication check

Run a read-only model catalogue request in a trusted environment after securely supplying your Meta key as `MODEL_API_KEY`:

```bash
python3 verify_model_access.py
```

The command queries only `GET /v1/models`, reports whether Muse Spark 1.3 is listed, and never prints the key. A successful check verifies API access, **not** quality of generated responses, account balance, or eligibility. The built-in unit tests use mocked clients and never contact Meta.

## Optional Muse Spark test

Install the OpenAI Python SDK:

```bash
python3 -m pip install 'openai>=1.0'
```

Set `MODEL_API_KEY` using an approved private credential source in your trusted environment (never commit it or paste it into issues). After confirming your Meta account billing controls, deliberately run:

```bash
python3 run_live.py --authorize-cost
```

This is a **paid/credited API request** path with a maximum of three Muse Spark calls per execution. It writes `live_result.local.json` with file mode 0600. Do not put that file in Git.

## Architecture

```text
Cited question + source excerpts
    └─> Director → JSON storyboard → Validation gate
                                       ↓
                            Independent Critic
                                       ↓
                   If issues: Repair → Validation gate
                                       ↓
                            Human science review
```

All stages are prompted separately when the optional real adapter is used. The offline UI demonstrates the intended scene trace with scripted fixtures only.

## Evidence caveats

The demo uses an externally readable NASA Space Place article as its original science reference:
https://spaceplace.nasa.gov/blue-sky/en/

The fixture contains a short paraphrase used for testing. **Matching a source ID does not establish that a claim is scientifically true.** The current review checks JSON structure and source IDs; semantic correctness, image consistency, and human evaluation still require independent assessment.

## Public demonstration

- [Three-minute video walkthrough](https://github.com/sahamgorengancuan-prog/youtube-automation/blob/claude/scientific-animation-studio-mvp-65rpn4/meta-science-director-2026/assets/SIAS_Meta_Offline_Demo_Walkthrough_180s.mp4) (honest offline presentation, no voiceover)
- [Temporary live web preview](https://vault-gmc-glasgow-ali.trycloudflare.com/) (offline fixture; may expire when the tunnel closes)

## Demo transparency

The offline preview is intentionally marked **PREVIEW_ONLY**, and reports `meta_inference_calls: 0`. The demo should not be represented as actual Muse Spark generation. Only verified `run_live.py` outputs may support live-model claims.

## Submission checklist

- [x] Locally running independent offline prototype
- [x] 20 local automated tests
- [x] Optional Meta Model API integration code
- [ ] Successful live Muse Spark response and measured metrics
- [x] Public GitHub repository folder URL
- [x] Three-minute captioned offline video walkthrough (no Muse inference; linked below)
- [ ] Cerebral Valley authenticated final submission confirmation

## Open source

MIT license. No API keys, browser cookies, production customer data, or private SIAS pipeline code are included.

## Project ownership

SimpliSains — Paskalis Glennardo. Built as a solo independent project, not on behalf of Meta or NASA.
