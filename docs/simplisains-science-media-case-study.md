# SimpliSains: an AI-native scientific communication field project

**Related work and running examples:** [SimpliSains](https://simplisains.site/) · [research-to-video architecture](https://simplisains.site/infrastructure/) · [science education methodology](https://simplisains.site/how-it-works/) · [actual videos](https://simplisains.site/science-videos/) · [YouTube channel](https://www.youtube.com/@simplisains_sias)

## Research question

Can a collection of specialized AI agents coordinate the repeated tasks of a scientific media operation—from identifying worthwhile questions and reviewing evidence to visual production, automatic quality checks, authorized publishing, and audience feedback—without routine manual production handoffs?

This question concerns **operational orchestration**, not a claim to conduct novel peer-reviewed scientific discoveries without researchers. The project aims to make science more accessible by producing engaging explanations of already studied phenomena.

## System design

A conceptual end-to-end process separates eight responsibilities:

1. **Discovery:** evaluate information demand, trends and recurring questions.
2. **Evidence retrieval:** gather sources and distinguish observation from inference.
3. **Scientific synthesis:** organize a coherent, testable explanatory model.
4. **Story direction:** structure a short narrative, script and storyboard.
5. **Audiovisual production:** coordinate illustrations, voice, subtitles and video rendering.
6. **Critique:** check defined properties and send recoverable errors back to repair.
7. **Distribution:** publish through previously authorized platform APIs and scheduling.
8. **Learning:** use post-publication signals to inform subsequent experiments.

Human ownership remains relevant to governance, permissions, escalation and correction. The claim of autonomy is limited to the **routine media production chain**, not to removing accountability.

## What a meaningful evaluation would measure

| Area | Candidate measurement | Important caveat |
| --- | --- | --- |
| Operational completion | end-to-end published runs / started runs | separate retry successes from permanent failure |
| Source grounding | fraction of externally checked core claims supported by suitable sources | no LLM-only self-assessment is a substitute for verification |
| Media coherence | timing errors, missing subtitles, visual continuity and defects | use documented rubrics and human reference ratings |
| Resource efficiency | cost per publishable video, elapsed time and external API spend | include hidden retries and moderation costs |
| Education | viewer comprehension of the explained concept | view counts and watch time are not direct learning outcomes |

No numeric performance claim in this document is presented as independently validated.

## Relevant literature

- Yao et al., **ReAct: Synergizing Reasoning and Acting in Language Models** (2023), https://arxiv.org/abs/2210.03629.
- Wu et al., **AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation** (2023), https://arxiv.org/abs/2308.08155.
- Madaan et al., **Self-Refine: Iterative Refinement with Self-Feedback** (2023), https://arxiv.org/abs/2303.17651.
- Shinn et al., **Reflexion: Language Agents with Verbal Reinforcement Learning** (2023), https://arxiv.org/abs/2303.11366.

These papers motivate possible orchestration patterns; they are not evidence that an independently operated scientific media system has achieved a particular error rate or educational benefit.

## Science explainers and source links

The [SimpliSains science-video archive](https://simplisains.site/videos/) is organized into topic collections to help visitors move from search questions to original YouTube episodes:

- [Space and astronomy](https://simplisains.site/topics/space-astronomy/): black holes, gravitational time dilation, and atmospheric meteors.
- [Ocean bioluminescence](https://simplisains.site/topics/ocean-bioluminescence/): chemically produced light and marine ecology.
- [Biology and animal behavior](https://simplisains.site/topics/biology-animal-behavior/): questions about behavior and living systems.

Original videos remain hosted on the [SimpliSains YouTube channel](https://www.youtube.com/@simplisains_sias), linked from each watch page.

## Relationship to this GitHub repository

The **Autonomous Scientific Animation Studio** in this repository is an experimental, portable Colab-first implementation that emphasizes reproducible scene generation and validation. It should not be conflated with every aspect of the separate SimpliSains production pipeline. Both explore machine-assisted scientific storytelling, but separate implementation branches can have different models, deployment environments and quality gates.

_Last revised: October 2026._
