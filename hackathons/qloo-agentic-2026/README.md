# SIAS Taste-to-Science — Qloo Agentic Hackathon 2026

Cultural intelligence for science storytelling.

Built for the Qloo Agentic Hackathon by the independent founder of SimpliSains (https://simplisains.site).

This isolated hackathon prototype extends an existing multi-agent science-media concept with new Qloo taste grounding. It does not change production SIAS.

## Pipeline

1. Taste Scout resolves an actual movie, musician, show, book, or game with Qloo GET /search.
2. Affinity Explorer fans out three live GET /v2/insights Qloo calls for movies, artists and books based on the Qloo entity ID.
3. Science Architect maps live cross-domain cultural affinities to editorially curated science concepts and 45-second storyboards.
4. Evidence Steward attaches scientific references from NASA, NOAA, NIDCD, and Britannica; Qloo is not represented as a factual science verifier.

The Qloo data cannot be replaced by a generic LLM because the relationship between input culture and resulting entities comes from live Qloo graph queries. Within-category rank is displayed; affinity scores are not compared across categories.

## Startup

Requires Python 3.8+ standard library and a valid Qloo hackathon API key.

Environment variables:
- QLOO_API_KEY (required for real requests; never expose to browser)
- QLOO_BASE_URL (defaults to https://hackathon.api.qloo.com)
- PORT (defaults to 18679)

Run with API key configured in a private environment:
  python3 server.py

Open http://127.0.0.1:18679

Health endpoint: GET /api/health
Curate endpoint: POST /api/curate with JSON such as {"seed":"Interstellar","type":"movie"}

If the hackathon key has not yet been issued, the backend deliberately returns HTTP 503. The frontend reports that accurately and displays only an explicitly labelled editorial illustration. It never fabricates Qloo responses.

To test:
  python3 -m unittest discover -s tests -v

## Licensing, provenance, and disclosure

- MIT license for the isolated hackathon code. Existing proprietary SIAS source is not included or relicensed.
- Qloo response data is not copied to this repository or cached publicly.
- API keys and Qloo personal account settings are never bundled in front-end assets.
- Input is a cultural title, not personal demographic data.
- Results are Qloo group-level affinities, not individual user predictions.
- Qloo API does not validate scientific truth; independent science sources are linked, and editorial review is necessary before video publication.
- The demo produces storyboards and metadata for handoff to SimpliSains, not completed videos.
- Production SIAS is not modified.

## Judge walkthrough

Enter Interstellar as a film or Daft Punk as a musician. The server resolves the cultural entity with Qloo Search, fetches cross-domain affinities with Qloo Insights, and presents actual titles plus their within-category rank. Scroll down for source-backed science hooks, video scene outlines, and an exportable JSON storyboard with a visible agent execution trace.

Project owner: Paskalis Glennardo — GitHub sahamgorengancuan-prog.
