# Submission form draft — NOT officially submitted

**Competition:** Meta Global AI Developer Hackathon, November 2026

**Solo entrant:** Paskalis Glennardo

**Track:** Indie Developer

**Theme:** Build for Your World

**Project title:** SimpliSains Science Director

**One-line pitch:** Turn science evidence into visually clear, traceable explanations through independent drafting and review agents.

## Problem

Short-form science content is easy to generate, but speed and visual polish can hide unsupported claims and misleading imagery. For learners, a credible-looking animation is not necessarily a correct one.

## What we built

An original standalone Python project that takes a science question and a small set of identifiable source excerpts. A director produces storyboard JSON with narrated scenes and visual directions; an independent critic challenges unsupported narration, mismatched images and bad citations; a repair step can make a targeted revision. A validation gate rejects fabricated source IDs and malformed scene structures. The application also includes an interactive offline preview of the scene trace.

The repository includes a **Meta Muse Spark 1.3 adapter**, using Meta Model API's documented OpenAI-compatible interface. The production inference path is intentionally separate from the zero-cost offline preview and requires an explicitly provided `MODEL_API_KEY` and cost acknowledgement.

## Evidence to date (November 3, 2026)

- 17 automated Python tests pass.
- The offline preview is running on VPS and can be used without credentials.
- Source fixture references a published NASA Space Place explainer.
- The model adapter is implemented but **successful authenticated Muse Spark inference remains unverified**.
- Source-ID validity does not establish scientific truth; expert semantic review remains necessary.
- No production SimpliSains channel or pipeline was modified.

## Links

Open-source repository folder: https://github.com/sahamgorengancuan-prog/youtube-automation/tree/claude/scientific-animation-studio-mvp-65rpn4/meta-science-director-2026

Three-minute captioned offline demonstration (video, no narration): https://github.com/sahamgorengancuan-prog/youtube-automation/blob/claude/scientific-animation-studio-mvp-65rpn4/meta-science-director-2026/assets/SIAS_Meta_Offline_Demo_Walkthrough_180s.mp4

Temporary working preview (offline, may expire): https://vault-gmc-glasgow-ali.trycloudflare.com/

## Next implementation steps

Complete official Muse Spark inference validation, benchmark hallucination and citation errors on held-out science questions, verify scene-to-narration accuracy with human reviewers, and evaluate latency and token consumption. These are **planned**, not completed results.

## Disclosure

This is an independent solo project. NASA's published page is used only as an external factual reference. Neither Meta, NASA nor Cerebral Valley has endorsed the app.

**Official final submission status:** Pending; Cerebral Valley browser verification rejects the remote session. Support was contacted November 3, 2026.
