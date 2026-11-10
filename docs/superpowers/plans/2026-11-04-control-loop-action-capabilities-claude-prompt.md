# Claude prompt: control-loop increment 2B

Work in `C:\uni\Bachelor-Thesis\autonomous-mixing-system` on the current branch. Implement only Task 1 (Steps 1–3) of `docs/superpowers/plans/2026-11-04-control-loop-action-capabilities-plan.md`, then stop uncommitted for Codex review. Use one implementer serially.

Read CLAUDE.md, engineering conventions, the full implementation plan, and both standing specs under `docs/superpowers/specs/`: `2026-11-04-control-loop-lifecycle-design.md` and `2026-11-04-control-loop-attempt-contract.md`. This request authorizes only the action-capability-view slice; preserve unrelated work.

Implement the four frozen lower-level capability records and `build_action_capability_view` with the exact signatures, validation, provenance and ordering in the plan. Derive gain bounds by intersecting explicit renderer support and policy, restricting target tracks to scope and omitting protected tracks. Equal endpoints remain valid; disjoint intervals are omitted. Invalid configuration must fail rather than silently return an empty view.

Keep records independent of admission/decision/run records so a later Situation can consume them without a cycle. The pure builder belongs in core.admission. Do not move existing permission classes or modify admit_action/preflight. No package exports or widened dependency rules.

This view describes declared available actions; it is not execution permission. Existing admission does not yet enforce the renderer descriptor/scope, so document that integration gap. Implement no adapter discovery, default gain bounds, EQ/dynamics/stereo, provider code, feedback history, No Change or Situation changes.

Follow test-first steps and the owned paths. Run the three restored mutation probes and all verification in the plan. No installs, dependency edits, accepted ADR changes, commits or pushes.

Handoff exact files/interfaces, red/green and mutation evidence, actual verification/skips, deviations and remaining gaps. If a material conflict or missing document prevents implementation, report it instead of guessing. Stop before the next slice.
