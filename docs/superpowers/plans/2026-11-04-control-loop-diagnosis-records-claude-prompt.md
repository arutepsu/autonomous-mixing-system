# Claude prompt: control-loop increment 2A

Work in `C:\uni\Bachelor-Thesis\autonomous-mixing-system` on the current branch. Implement only Task 1 (including Steps 1–3) of `docs/superpowers/plans/2026-11-04-control-loop-diagnosis-records-plan.md`, then stop uncommitted for Codex review. Work serially with one implementer.

Read CLAUDE.md, engineering conventions, the source-lifecycle brief `docs/superpowers/specs/2026-11-04-control-loop-lifecycle-design.md`, the approved `2026-11-04-control-loop-attempt-contract.md` beside it, and the implementation plan in full. This request authorizes the diagnosis-records slice only; preserve unrelated work.

Add the two typed identifiers, immutable diagnosis/evidence/coverage records and `validate_diagnosis_references` with the exact signatures, enum values and invariants in the plan. Use explicit coverage references so a candidate cannot silently borrow another method/region. Preserve current-checkpoint provenance rules: older production revisions are valid for retained members; superseded artifacts are not valid current evidence.

This is descriptive contract groundwork. Implement no detector, DSP method, calibrated confidence, action gate, provider code, Situation extension or core.diagnosis behavior package. Do not invent thresholds or infer musical roles. D11's scientific method/calibration decision stays open.

Follow the owned paths and test-first sequence, narrow records-architecture updates, three restored mutation probes, documentation limits and verification commands in the plan. Keep existing source definitions unchanged apart from adding the two identifier classes. No dependencies, package exports, accepted ADR changes, commits or pushes.

Report exact files/interfaces, red/green evidence, mutation results, actual checks/skips, deviations and remaining gaps. If a material conflict or missing document prevents implementation, report it instead of guessing. Stop before the next slice.
