# ADR-0004 — Style lock before production scenes
Status: accepted.
Context: style drift across scenes is the top failure of generated illustration sets.
Decision: master style board + character/prop sheets + environment anchors must exist and pass dual review AND human approval (`human_status: APPROVED`) before any production scene is generated. References follow a fixed hierarchy with recorded selection.
Alternatives: per-scene prompting with style adjectives — rejected ("style is a system, not a prompt adjective").
Consequences: upfront cost before first scene; drastically better consistency.
