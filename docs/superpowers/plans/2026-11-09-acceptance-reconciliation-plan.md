# Acceptance Reconciliation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans sequentially in this session.

**Goal:** Combine an acceptance authority answer and integrity evidence into a truthful coordinator outcome.

**Architecture:** Immutable reconciliation outcomes in core.records.reconciliation. Pure composition in core.loop.reconciliation. Reuse acceptance and integrity response validators.

**Tech Stack:** Existing Python, pytest, Ruff and uv.

**Spec:** Control-loop attempt contract sections 22, 28 and 29. Confirming committed candidate acceptance and confirming retention of the prior state are distinct outcomes. This function interprets supplied evidence; a future coordinator performs bounded port calls and records reconciliation before ordinary work resumes.

## Records

ReconciliationOutcome enum members have exact lowercase-name string values:

- CONFIRMED_ACCEPTANCE
- VERIFIED_PREVIOUS_STATE
- ACCEPTED_STATE_FAILED
- ACCEPTED_STATE_UNCERTAIN
- PREVIOUS_STATE_FAILED
- PREVIOUS_STATE_UNCERTAIN
- AUTHORITY_UNCERTAIN

Frozen ReconciliationResult(outcome: ReconciliationOutcome, authoritative_reference: AcceptedReference | None, verified_reference: AcceptedReference | None).

Check types. AUTHORITY_UNCERTAIN requires both references None. Every other outcome requires authoritative_reference. CONFIRMED_ACCEPTANCE and VERIFIED_PREVIOUS_STATE require verified_reference == authoritative_reference. The four integrity-failure/uncertainty outcomes require verified_reference None. This distinguishes known journal authority from verified usable state, including when the authoritative candidate is damaged.

## Pure function

`reconcile_acceptance(event: AcceptanceEvent, authority: AcceptanceQueryResult, *, integrity_request: StateIntegrityRequest | None, integrity_report: StateIntegrityReport | None) -> ReconciliationResult`

All four arguments are required, including explicit None values.

1. Validate the event with validate_acceptance_event. Validate authority using validate_acceptance_query_response(AcceptanceQuery(event.run_id, event.acceptance_id), authority). Propagate malformed inputs and unexpected errors.
2. AcceptanceAuthorityUnknown requires both integrity fields None and returns AUTHORITY_UNCERTAIN. Do not allow an intact previous state to substitute for unknown journal authority.
3. AcceptanceFound must carry an event exactly equal to the submitted event, not merely the same run/acceptance IDs. Its authoritative reference is authority.accepted. Require an integrity request for that exact candidate state, then validate the report against that independently supplied request.
4. AcceptanceAbsent uses event.expected as the authoritative reference. Require an integrity request for exactly event.expected.state, then validate its report. This branch relies on the approved serial-run discipline: no other acceptance is dispatched while this event is unresolved. Absence alone is not a general-purpose latest-head query for concurrently advancing external writers.
5. For the found branch, VERIFIED/FAILED/UNCERTAIN integrity maps to CONFIRMED_ACCEPTANCE/ACCEPTED_STATE_FAILED/ACCEPTED_STATE_UNCERTAIN.
6. For the absent branch, VERIFIED/FAILED/UNCERTAIN maps to VERIFIED_PREVIOUS_STATE/PREVIOUS_STATE_FAILED/PREVIOUS_STATE_UNCERTAIN.

Only the two verified outcomes populate verified_reference. Even on integrity failure the known authoritative reference remains reported. Use exhaustive enum/type handling. Original state/reference objects are retained. Exact request/report equality also binds root configuration choices through the request's logical binding tuple.

No journal, filesystem or clock calls occur. The function performs no selection/publication, retry, rollback receipt construction or recording. A later coordinator must record the reconciliation and confirm the relevant selection before creating a VERIFIED RollbackReceipt for the previous-state branch. Candidate-confirmation branches never produce rollback receipts. If reconciliation recording fails, the coordinator terminates for recording failure. Port calls remain bounded under recovery/time gates.

## Owned paths

Read CLAUDE.md, applicable AGENTS.md and required references. Preserve existing work and use labelled metadata fixtures. Generic project and track identities apply to all tests. No installs, dependencies, commits or pushes.

- Add src/autonomous_mixing/core/records/reconciliation.py and src/autonomous_mixing/core/loop/reconciliation.py.
- Add tests/unit/test_acceptance_reconciliation.py.
- Update tests/architecture/test_core_records.py with the two names and exact used owners acceptance and invariants. Update tests/architecture/test_core_layers.py with loop.reconciliation and reconcile_acceptance. Preserve all current restrictions and initializers.
- Update overview in place and append research-log evidence and limits. Keep requirement statuses.

## Task 1

- [ ] Record baseline HEAD/status and inspect acceptance/integrity records and validators. Run baseline checks.
- [ ] Write meaningful red tests for the seven outcomes, result shapes, exact original reference preservation, missing integrity arguments, mismatched candidate/previous state, matching revision with altered metadata, changed root bindings, wrong run/acceptance identity and a found event with altered contents under the same IDs. Include unknown authority with tempting verified previous-state evidence and assert rejection.
- [ ] Use real pure candidate derivation and verification to build acceptance fixtures. Include known candidate authority with failed integrity and verify the authoritative reference is retained while verified_reference is None.
- [ ] Implement records and composition, add exact inventories, and run focused, acceptance, integrity and architecture tests.
- [ ] Document the serial-run assumption and deferred selection/recording. Run full suite, Ruff lint/format, CLI help and uv lock --check, using uv run --locked where available. Report omitted checks.
- [ ] Review scope and return exact signatures, red/green evidence, verification, deviations, remaining coordinator obligations and git status. Stop uncommitted for Codex review.
