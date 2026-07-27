# Known limitations

1. **No live provider verification.** Zero paid calls were made in this build.
   All adapters (BFL, OpenAI, OpenRouter, Higgsfield) are mock-tested; payload
   field names are isolated per adapter and marked `live_verified: false`.
   The provider canary (Section 8) is the designed first live test.
2. **Offline plan output is a draft.** Without an OpenAI key, claims are
   clearly-marked unsourced drafts (source_guard flags them) and the script is
   shorter than the 45–75 s target (validator WARNs). Live keys produce the
   real research/script.
3. **Live style-lock/pilot/production loops are notebook-driven.** The guarded
   sections wire the tested engine primitives (tournament, dual review,
   repair, TTS, alignment, render) but the full live loop has not run —
   blocked on keys by design.
4. **Colab-specific paths untestable here.** `google.colab` (Drive mount,
   userdata, files.download) is unavailable in this build environment; those
   paths are guarded and the non-Colab fallbacks are what CI executes.
5. **Higgsfield endpoint shapes are best-effort** against the documented
   surface at the pinned commits; the adapter is the single fix point.
6. **`audioop` deprecation** (silence gate) — works on Python 3.10–3.12;
   Colab currently ships 3.11/3.12. Swap planned when Colab moves to 3.13.
7. **label_reveal / page_turn** currently render as holds (engine limitation,
   open-30% visual treatment).
8. **BFL skills repo docs** live at non-`main` paths; guidance was taken from
   patterns already encoded in the engine's BFL adapter rather than that repo's
   files.
9. **The simulated-provider preflight proves parsing, not provider behaviour.**
   Its transports reproduce the response *shapes* the real APIs return
   (including the ones that broke live runs), so response-handling bugs are
   caught before money is spent. It cannot tell you that BFL's live endpoint
   still exists, that a model slug is still served, or what an image looks
   like. `simulated_adapters()` is reachable only by passing it explicitly to
   `run_all(adapters=...)`; nothing in the notebook's live path can fall back
   to it.
10. **The standalone notebook is a snapshot.** Its embedded payload is the repo
    at build time. `test_payload_is_in_sync_with_the_repo_sources` fails the
    moment the sources move ahead of it — rebuild with `make standalone`.
