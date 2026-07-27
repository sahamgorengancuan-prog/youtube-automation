# ADR-0005 — FFmpeg baseline renderer
Status: accepted.
Context: renderer must be deterministic, dependency-light, and verifiable offline.
Decision: FFmpeg builds per-scene clips, concatenates, muxes; ffprobe validates streams/duration; blackdetect guards blank frames. Remotion may be added later as an optional renderer (captions/layouts), but FFmpeg output remains the validation reference.
Alternatives: MoviePy (slow, fragile), Remotion-only (Node dependency in the critical path — the legacy pipeline lost a full production run to a single `npm install` exit 1).
Consequences: sophisticated caption animation deferred to the optional Remotion layer.
