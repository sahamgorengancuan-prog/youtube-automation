# Style canon — Scientific Notebook Cartoon

Original identity based on high-level editorial illustration traits; not an
imitation of any named studio.

## Mandatory traits
warm off-white notebook paper; faint square grid; generous margins; dark
graphite/ink contours of mostly uniform width with tiny handmade wobble; simple
geometric construction; rounded corners; clear silhouettes; readable human
poses; restrained role-based palette (ink/paper/primary/attention/danger/
nature/secondary — configurable in `configs/default.yaml`); small hand-drawn
annotations; margin hypothesis notes; orange circle around surprising details;
tiny scale ruler; causal arrows; page-turn corner motif; light visual humor.

## Anatomy rules
Every recurring human keeps face geometry, hair silhouette, outfit, body
proportions, skin tone, accessory placement, limb construction and gesture
vocabulary. Every human: one head, one torso, two arms, two legs, logically
connected joints; no duplicated limbs, detached hands, or floating parts.
Occlusion only when composition makes it obvious.

## Forbidden outcomes
abstract art; surrealism; psychedelic logic; body deformation; missing or
duplicated limbs; disconnected parts; photorealism; glossy 3D mascots; sticker
collage; corporate vector art; crowded infographics; large hallucinated text
blocks; random icons without story function; no-limbs characters; empty
decorative backgrounds; style drift. Encoded as `style.bible.FORBIDDEN` and the
hard-fail taxonomy (`HF_*` codes).

## Reference hierarchy
style board → character sheet → prop sheet → environment anchor → previous
approved scene (only when continuity requires) → pose sketch → special objects.
Selection is recorded with reasons, omissions and hashes
(`style/reference_pack.py`). Never send everything indiscriminately.

## Continuity strategy
`continuity_refs` on a SceneSpec is the only trigger for chaining a previous
scene as a reference (`style/continuity.py`).
