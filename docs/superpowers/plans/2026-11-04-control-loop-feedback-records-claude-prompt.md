# Claude prompt: control-loop increment 2C

Work in `C:\uni\Bachelor-Thesis\autonomous-mixing-system` on the current branch. Implement only Task 1, Steps 1–3, of `docs/superpowers/plans/2026-11-04-control-loop-feedback-records-plan.md`, then stop uncommitted for Codex review. Work serially with one implementer.

Read CLAUDE.md, engineering conventions, both approved source-lifecycle/attempt-contract specs in `docs/superpowers/specs/`, and the full implementation plan. This request authorizes only the compact feedback-record slice; preserve unrelated work.

Add DecisionCallId, the exact typed feedback payloads/union, DecisionFeedback, DecisionHistory and validate_decision_history. Follow the plan's signatures, enum values, shape/linkage rules and file ownership. A failed call must remain representable without a valid DecisionId; a rejected or partial action must never look accepted.

Keep records below decision/admission/run to avoid cycles. Do not embed full Situation, Decision, RunRecord, CorrectionAttempt, raw provider text or sample arrays. Historical observations retain their original state/provenance; never validate them against the current checkpoint merely because they appear in the next decision input.

This is descriptive history groundwork, not the full trace, a projection generator, physical restoration proof or runtime policy engine. Implement no Situation integration, actual Decision No Change variant, provider code, retries, journal, detector or orchestration.

Follow test-first steps, narrow architecture updates, all three restored mutation probes, documentation limits and verification in the plan. No dependency changes/installations, package exports, accepted ADR edits, commits or pushes.

Handoff exact interfaces/files, red/green and mutation evidence, actual checks/skips, deviations and remaining gaps. Report a missing document or material contract conflict instead of guessing. Stop before the next increment.
