# Claude prompt — increment 2E

The owner approves the decision-outcome schema and authorizes execution of its plan. Implement this increment only in C:/uni/Bachelor-Thesis/autonomous-mixing-system, sequentially yourself, without subagents, and stop uncommitted for Codex review.

Read:
- docs/superpowers/specs/2026-11-05-decision-outcomes-design.md
- docs/superpowers/plans/2026-11-05-decision-outcomes-plan.md
- CLAUDE.md and its required references
- docs/superpowers/specs/2026-11-04-control-loop-attempt-contract.md, sections 12–15

Verify actual repository state and preserve existing work, including uncommitted Situation integration if present. Missing files must be reported by exact path, not replaced with invented contracts.

Implement NoChange, ActionJustification, required candidate_ids/justification on ProposeAction, nonempty requested_context for MISSING_CONTEXT and request-aware candidate/evidence checks exactly as specified. Retain enum values, lower-validator signatures, existing admission failure order and unrelated behavior. Do not add a semantic sufficiency gate or claim a proposal is authorized.

Follow owned paths, meaningful red tests, explicit fixture migration and final checks. Preserve old test assertions except intentional contract changes; metadata test doubles are not scientific detections. No installs, dependency changes, commits or pushes. Return the evidence-based handoff and stop.
