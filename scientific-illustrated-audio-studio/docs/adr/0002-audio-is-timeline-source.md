# ADR-0002 — Audio duration is the source of truth
Status: accepted.
Context: pre-assigned scene durations force either stretched images or clipped narration.
Decision: approve script → synthesize narration → verify → transcribe → align words → derive scene timings; final video duration must match narration within tolerance (default 0.08 s).
Alternatives: storyboard-first timing — rejected.
Consequences: render depends on alignment artifacts; scenes merge/split based on spoken reality.
