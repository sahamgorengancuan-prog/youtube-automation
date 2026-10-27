# SIAS Taste-to-Science — Judge Walkthrough

Live demo: https://taste.169-58-205-190.sslip.io/

Open source: https://github.com/sahamgorengancuan-prog/youtube-automation/tree/hackathon/qloo-agentic-2026/hackathons/qloo-agentic-2026

The idea: People love movies, music, games, books. SIAS uses Qloo's real cross-domain affinities to create an *audience-aware* science editorial brief, grounded by separate scientific source references and convertible into an animated science explainer via the existing SimpliSains ecosystem.

## Run-through (when Qloo hackathon key is issued and validated)
1. Confirm top-right status LIVE QLOO ENGINE.
2. Choose Interstellar / Movie, click Explore affinities.
3. Taste Scout resolves real Qloo movie entity with /search.
4. Affinity Explorer calls /v2/insights three times with signal.interests.entities and distinct filter.type values.
5. Inspect returned movies, artists, books, and their source-category rank.
6. Science Architect produces six science story treatments, each a 45-second storyboard.
7. Evidence Steward adds independent science references; Qloo never pretends to validate scientific truth.
8. Export storyboard JSON or read the transparent agent trace.

## Developer setup
Run the isolated Python 3 program with the personal hackathon QLOO_API_KEY environment variable. It connects exclusively to https://hackathon.api.qloo.com. Run the unit tests using python3 -m unittest discover -s tests -v. When a key is not provided, it refuses to generate fictitious Qloo results and reports HTTP 503 from /api/curate.

## Authenticity
The existing SIAS full media pipeline predates the challenge. The new Qloo-specific service, frontend, agent pipeline and export feature were created as a separate MIT-licensed prototype. No private production SIAS source or credentials are bundled. Qloo affinities are based on aggregate cultural patterns, not an individual's behavioral prediction.

[exit code: 0]
