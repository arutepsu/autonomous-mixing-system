# Dataset and Controlled Project Plan — v1

## Source
Primary: original self-created multitrack projects with aligned WAV stem exports. DAW: FL Studio for composing/exporting; Python operates on stems.

## Candidate size (planning only)
- Calibration: 6 projects (2 per genre family).
- Held-out evaluation: 9 projects (3 per genre family).
- These are provisional targets, not a statistical sample-size justification.

## Genre families
House/techno; trap/phonk/wave; drum & bass.

## Recommended variants
For each relevant project: clean/reference condition where defensible, controlled defect variant with parameterized modification, and optional manual correction reference. Preserve all injection parameters and random seeds.

## Required manifest fields (proposal)
`project_id`, `genre_family`, `split`, `sample_rate`, `stem_files`, `track_roles`, `time_alignment`, `variant_id`, `defect_generation`, `producer_intent`, `license_or_ownership`, `notes`.

## Leakage prevention
Partition by underlying musical project, not by rendered variant: all variants from a source project must remain in the same split. Freeze detector thresholds and acceptance criteria before final evaluation.

Hidden-reference boundary (requirements SR-10, ADR-010): the clean/reference condition, any manual correction reference and the `defect_generation` field (injection type, target tracks, parameters, seeds) are hidden evaluation data. They are stored separately from the runtime manifest, never reach controllers, decision inputs or runtime validation, and are joined with run records only after execution for research evaluation. `variant_id` values must not encode injection details.

## Validity risks
Injected faults are not automatically real-world mixing problems. Ask whether each example is actually perceptually relevant; do not claim expert ground truth without independent listening/annotation.

## Open questions
- Human listening or expert ratings as reference labels?
- Which defect intensities can be reproducibly created without trivial solutions?
- How to ensure clips are loudness-matched for perceptual comparisons?


## Feasible pilot proposal (2026-11-23; not a new sample-size commitment)

The numerical targets above remain provisional; the optional six-project/three-listener calibration study is not adopted. The owner prioritizes a feasible agent/tool/feedback thesis. The [experimental policy and evaluation proposal](p1-experimental-policy-pilot.md) recommends keeping inspected HT01/trap material in development and reserving one genuinely independent new project before algorithm/agent outcomes are examined. A second held-out project is optional if feasible. More variants/repeats of one project do not become new independent projects. No new split is assigned or evaluation authorized by this note; a small evaluation supports limited case-study claims, not statistical generalization.
