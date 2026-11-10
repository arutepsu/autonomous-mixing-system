# Acceptance Contract Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans sequentially in this session.

**Goal:** Define candidate acceptance events, journal outcomes and a port for commit/query, with pure coordinator-side reference validation.

**Architecture:** Immutable records in core.records.acceptance. Protocol and response checks in core.ports.acceptance. Cross-stage event validation in core.loop.acceptance. A subsequent task implements the in-memory journal.

**Tech Stack:** Existing Python, pytest, Ruff and uv.

**Spec:** Approved acceptance/journal split and control-loop attempt contract section 29. The first runnable milestone is NON_DURABLE. Initial real audio is supplied by the owner through FL Studio exports. All track and project identities remain generic.

## Exact records

Add AcceptanceId to identifiers using the existing identifier pattern. Define frozen records in core.records.acceptance with required fields and no defaults:

- AcceptedReference(state: ProjectState, sequence: int). Sequence uses require_count, minimum zero.
- AcceptanceEvent(acceptance_id: AcceptanceId, run_id: RunId, attempt_id: AttemptId, decision_id: DecisionId, expected: AcceptedReference, action: Action, execution: ExecutionReceipt, assessment: VerificationAssessment, verification: VerificationResult). The complete candidate is assessment.assessed.state. The expected sequence is the acceptance sequence, independent of future all-event journal sequence numbers.
- AcceptanceCommitted(event: AcceptanceEvent, accepted: AcceptedReference). Require accepted.state == event.assessment.assessed.state and accepted.sequence == event.expected.sequence + 1.
- AcceptanceNotCommitted(acceptance_id: AcceptanceId, explanation: str).
- AcceptanceCommitUnknown(acceptance_id: AcceptanceId, explanation: str).
- AcceptanceCommitResult = AcceptanceCommitted | AcceptanceNotCommitted | AcceptanceCommitUnknown.
- AcceptanceQuery(run_id: RunId, acceptance_id: AcceptanceId).
- AcceptanceFound(event: AcceptanceEvent, accepted: AcceptedReference). Apply the same state/sequence consistency as AcceptanceCommitted. This reports that event's historical commit, rather than claiming it is the journal's latest head.
- AcceptanceAbsent(run_id: RunId, acceptance_id: AcceptanceId).
- AcceptanceAuthorityUnknown(run_id: RunId, acceptance_id: AcceptanceId, explanation: str).
- AcceptanceQueryResult = AcceptanceFound | AcceptanceAbsent | AcceptanceAuthorityUnknown.

Type-check all fields. All explanation strings are nonblank. Constructors check embedded consistency. AcceptanceEvent requires ATTEMPT assessment, before evidence present and exactly equal to expected.state, ACCEPTED ATTEMPT verification, result ID/criteria ID/version matching the assessment, shared project identity among expected/candidate/action/execution, action expected revision and receipt before revision matching expected.state.revision, and CANDIDATE_READY execution at the candidate revision with the same action ID. Candidate revision must differ from expected.state.revision. Numerical verification correctness and action-specific derivation are established by the loop validator below.

## Pure event validation

`validate_acceptance_event(event: AcceptanceEvent) -> None` in core.loop.acceptance:

- Check event type.
- Derive the expected candidate using derive_candidate_project_state(event.expected.state, event.execution, event.action, candidate.audio.checkpoint_id, candidate.snapshot.captured_at). Inspect the actual snapshot timestamp field and use its existing name rather than inventing one. Require exact equality with event.assessment.assessed.state. This reuses the existing gain, supersession, retained-artifact and processing-state consistency rules.
- Recompute assess_verification(event.assessment) and require equality with event.verification.

This function is the coordinator's precommit obligation. The port and future adapter cannot import loop or stage internals. Run/attempt/decision membership, authoritative run criteria and actual rendered-file integrity still need coordinator evidence. An ACCEPTED verification result is a mathematical assessment rather than proof of file integrity.

## Journal protocol and response checks

Define AcceptanceJournal(Protocol), without runtime_checkable:

- commit(self, event: AcceptanceEvent) -> AcceptanceCommitResult
- query(self, request: AcceptanceQuery) -> AcceptanceQueryResult

Document the future implementation's atomic logical contract. Each journal is configured for one run and an initial AcceptedReference at sequence zero. A new event must name the configured run and expected current reference. On commit, recording the event and advancing the accepted reference are one logical operation. Exact duplicate IDs/events return their original committed acknowledgement even after later acceptances. A reused ID with different contents, a wrong run or stale expected reference returns AcceptanceNotCommitted and changes nothing. AcceptanceCommitUnknown makes no claim whether the event was stored. A query searches only the supplied run and acceptance identity. No blind new acceptance after an unknown outcome.

`validate_acceptance_commit_response(event: AcceptanceEvent, response: AcceptanceCommitResult) -> None` checks argument types. A committed response must contain exactly the submitted event and corresponding accepted reference. Other responses must match its acceptance ID.

`validate_acceptance_query_response(request: AcceptanceQuery, response: AcceptanceQueryResult) -> None` checks argument types and exact run/acceptance identity. A found event must name that run and ID. Never turn an invalid response into confirmed absence.

The port has no generic retryable failure result that erases commit uncertainty. Unexpected implementation errors propagate. Query resolution is distinct from physical integrity and from restoration receipts. A future coordinator bounds authority queries and integrity checks under recovery/time gates.

## Owned paths

Read CLAUDE.md, applicable AGENTS.md and required references. Preserve other work. No installs, dependencies, commits or pushes.

- Modify src/autonomous_mixing/core/records/identifiers.py for AcceptanceId only.
- Add src/autonomous_mixing/core/records/acceptance.py, src/autonomous_mixing/core/ports/acceptance.py and src/autonomous_mixing/core/loop/acceptance.py.
- Add tests/unit/test_acceptance_contracts.py.
- Update tests/architecture/test_core_records.py with the new ID, records inventory and exact used imports from actions, identifiers, invariants, project, verification and verification_assessment. Update tests/architecture/test_core_layers.py with the protocol, response validators and validate_acceptance_event ownership using the current inventory conventions. Keep existing rules and docstring-only initializers.
- Update overview in place and append research-log evidence and limits. Keep requirement statuses.

## Task 1

- [ ] Record actual HEAD/status. Inspect reused function signatures and snapshot fields. Run baseline tests and tool checks.
- [ ] Write meaningful red tests for record shapes and required fields, sequence zero and increment, event cross-links, acknowledged/query response identity, wrong-run query, stale or altered candidate state, tampered accepted verification and duplicate-evidence consistency. Use labelled metadata fixtures, real pure verifier calls and existing candidate derivation.
- [ ] Implement records, protocol, response validators and pure event validator. No journal adapter or dispatch workflow in this task. Update inventories and run focused, verifier and architecture tests.
- [ ] Confirm the event validator catches both action/state inconsistencies and altered verification results. Protocol test doubles establish only response contracts. Idempotent storage and compare-and-set behavior will receive tests with the next in-memory adapter.
- [ ] Update docs and run full suite, Ruff lint/format, CLI help and uv lock --check, using uv run --locked where available. Report any omitted checks.
- [ ] Review exact file scope and return signatures, red/green evidence, results, deviations, open coordinator bindings and git status. Stop uncommitted for Codex review.
