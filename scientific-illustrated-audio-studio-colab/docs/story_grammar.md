# Story grammar

## Beat grammar (locked)
B01 cold_open (normal→impossible) · B02 fact_1 (recognition→concern) ·
B03 fact_2 (concern→surprise) · B04 fact_3 (surprise→anticipation) ·
B05 explanation (confusion→understanding) · B06 scale_example
(understanding→awe) · B07 gasp_reveal (awe→gasp) · B08 payoff
(gasp→satisfying click). 7–9 scenes allowed; emotional logic preserved
(`story/beats.py::validate_beats`).

## Hook rules
Two variants — consequence-first and contradiction-first — scored on immediate
curiosity, visualizability, truthfulness, spoken naturalness, promise-to-payoff
consistency, overclaim avoidance. One must be selected before proceeding.

## Catchphrase
"Your intuition skipped a page—let's put the science back." (ID variant:
"Intuisimu melewatkan satu halaman—sekarang kita isi dengan sains.")
Exactly once; near the intuition→mechanism transition (default: opening of
B05); never the first sentence; never in the payoff. A/B variants via
`story.catchphrase` config; one canonical default.

## Spoken constraints
45–75 s; 145–165 wpm; 8–18-word sentences; one claim per sentence; curiosity
escalation every 8–12 s; contractions welcome; forbidden: "In this video",
"Today we will learn", "As an AI", lecture cadence, fact-list endings.

## Reveal alignment
Each gasp_reveal scene carries a `reveal_word`; captions uppercase it and the
visual reveal must land on its timestamp (checked in QC).
