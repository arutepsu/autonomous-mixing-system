# Ordered Run Recording and Completion Contracts

> **For agentic workers:** Use superpowers:executing-plans sequentially in this session.

**Goal:** Define typed ordered recording and terminal/completion records required by the first serial coordinator.

**Architecture:** Lower-level termination codes, trace records downstream of runtime records, and a RunJournal Protocol extending AcceptanceJournal. Existing acceptance events remain the authoritative accepted-state history. Journal storage and coordinator effects follow in integration tasks.

**Tech Stack:** Existing Python, pytest, Ruff and uv.

**Spec:** Approved docs/superpowers/specs/2026-11-09-controlled-coordinator-design.md and attempt-contract sections 25â€“30. This is the remaining contract portion of design Task 1.

## Scope

Implement only the core contracts and tests in this plan. The next coordinator implementation includes an in-memory RunJournal adapter composed with the existing acceptance journal, dispatch/recovery policies and actual feedback projection. Preserve existing work. No installs, dependencies, commits or pushes.

Read CLAUDE.md, applicable AGENTS.md and required project references. All core records are frozen with concrete immutable fields. Initializers remain docstring-only. Generic track identities apply. Test metadata does not establish scientific audio performance.

## Termination codes and RunRecord migration

Add RunEventId to identifiers using the existing identifier pattern.

Add core.records.termination defining TerminationReason. Exact values are lowercase member names. Use the complete section 27 reason groups:

- SUCCEEDED: INITIAL_CRITERIA_MET, CANDIDATE_CRITERIA_MET.
- ABSTAINED: NO_CHANGE_PROPOSED, INSUFFICIENT_EVIDENCE, MISSING_CONTEXT, CONFLICTING_CONSTRAINTS, NO_JUSTIFIED_ACTION.
- LIMIT_REACHED: DECISION_CALL_LIMIT, ATTEMPT_LIMIT, RENDER_CALL_LIMIT, RECOVERY_CAPACITY_LIMIT, ELAPSED_TIME_LIMIT.
- FAILED: INITIAL_ANALYSIS_FAILED, INITIAL_ASSESSMENT_INCONCLUSIVE, REQUIRED_BASELINE_UNAVAILABLE, DECISION_FAILED, INVALID_DECISION_RESPONSE, DECISION_RETRIES_EXHAUSTED, EXECUTION_PREPARATION_FAILED, RENDER_FAILED, INVALID_RENDER_RESPONSE, CANDIDATE_ANALYSIS_FAILED, VERIFICATION_INCONCLUSIVE, ACCEPTED_STATE_INTEGRITY_FAILED, ACCEPTED_STATE_INTEGRITY_UNCERTAIN, RESTORATION_FAILED, RESTORATION_UNCERTAIN, REQUIRED_RECOVERY_NOT_PERFORMED, RECORDING_FAILED, ACCEPTANCE_COMMIT_UNCERTAIN, RUNTIME_CONTRACT_FAILED.
- CANCELLED: USER_CANCELLED.

Change RunRecord.termination_reason to TerminationReason | None and append required termination_explanation: str | None. FINISHED requires the enum, its matching RunOutcome group and nonblank explanation. Active states require all four terminal fields None. Retain all other current invariants and fields. Keep reason-to-outcome mapping private in records.run, where RunOutcome is owned, avoiding a terminationâ†’run cycle.

This is an intentional API break. Migrate constructors in tests/unit/test_run_records.py and test_execution_records.py explicitly. Preserve every unrelated assertion. Positive tests covering multiple coarse outcomes must supply an appropriate reason per group. New tests pin enum values and incompatible reason/outcome pairs. Reachability of actual runtime terminal paths is a coordinator test obligation, not established by enum construction tests.

## Dispatch and observation records

Add core.records.run_trace.

OperationKind members are ANALYZE, DIAGNOSE, DECIDE, RENDER, QUERY_ACCEPTANCE, INSPECT_STATE, valued as their lowercase names.

StageRequest is AnalysisRequest | DiagnosisRequest | Situation | RunRenderRequest | AcceptanceQuery | StateIntegrityRequest. Each operation pairs with the corresponding request type in the order above.

DispatchIntent(operation: OperationKind, request: StageRequest, accepted: AcceptedReference, call_id: DecisionCallId | None, attempt_id: AttemptId | None, execution_id: ExecutionId | None).

- Validate types and operation/request pairing. DECIDE requires call_id and forbids attempt/execution IDs. RENDER requires all three IDs and execution_id == request.plan.execution_id. Other operations permit optional typed IDs to capture initialization or recovery context.
- Request state must equal accepted.state for ANALYZE only when analyzing the accepted state is claimed by the caller; candidate analysis legitimately uses a tentative state. Therefore do not impose equality here. Coordinator stage tests establish each correct relationship. For DECIDE require request.state == accepted.state. For RENDER require request.before.state == accepted.state. Same-project checks apply whenever the request has a state.

DispatchSkipped(reason: TerminationReason, explanation: str), with nonblank explanation. It records a completed slot without dispatch. Allowed reasons are USER_CANCELLED, ELAPSED_TIME_LIMIT, RECORDING_FAILED and RUNTIME_CONTRACT_FAILED.

InvalidPortResponse(type_name: str, explanation: str), both nonblank. Records a malformed in-process response without an unconstrained Python payload. Do not dump arbitrary repr values or credentials. Actual serialized provider request/response capture is a later adapter requirement.

OperationObserved(intent_id: RunEventId, operation: OperationKind, outcome: concrete union described below).

Valid outcomes:
- ANALYZE: AnalysisResponse | PortFailure.
- DIAGNOSE: DiagnosisReport | PortFailure.
- DECIDE: DecisionResponse | PortFailure.
- RENDER: RenderedCandidate | RenderFailure | RenderPreparationFailure.
- QUERY_ACCEPTANCE: AcceptanceFound | AcceptanceAbsent | AcceptanceAuthorityUnknown.
- INSPECT_STATE: StateIntegrityReport.
- DispatchSkipped and InvalidPortResponse are allowed for every operation.

Validate the operation/outcome pairing. Store the original result rather than wrapping successful responses in Success again. No arbitrary object/Any fields or string-only substitutes for valid typed responses.

## Terminal snapshot and ordered trace

TerminalSnapshot(run: RunRecord, authoritative: AcceptedReference | None, verified: AcceptedReference | None).

Require FINISHED run, matching project on supplied references and verified == authoritative when verified is present. SUCCEEDED requires a verified reference. For a known FinalProjectState (anything except UNKNOWN), require a verified reference. UNKNOWN requires verified None and may retain a known authoritative reference. Do not equate a failed integrity check with unknown journal authority.

RunTracePayload is the concrete union of:
- RunRequest, DispatchIntent, OperationObserved;
- existing gate results: InvalidResponse, KeepState, RequestContext, DeclineAction, EvidenceBlocked, ReadyForAdmission;
- existing admission/preflight results: AdmittedAction, RejectedAction, MetadataReady, PreflightRejected;
- BudgetBlock, ExecutionReceipt, CorrectionAttempt;
- VerificationAssessment, VerificationResult;
- AcceptanceEvent, AcceptanceCommitted, AcceptanceNotCommitted, AcceptanceCommitUnknown;
- ReconciliationResult, DecisionFeedback, TerminalSnapshot.

Use only these concrete types. Intermediate stage records retain their own identities. More than one trace entry may cite the same immutable object.

RunTraceEvent(event_id: RunEventId, sequence: int, run_id: RunId, created_at: datetime, usage: BudgetUsage, payload: RunTracePayload). Sequence is an integer >=1, created_at UTC. Validate concrete payload types. If payload carries a run ID (RunRequest, DecisionFeedback, CorrectionAttempt, AcceptanceEvent, committed event or TerminalSnapshot.run), require agreement. For DispatchIntent DECIDE, Situation.run_id must agree. A TerminalSnapshot's run.usage must equal this event's usage.

RunJournalSnapshot(run_id: RunId, initial: AcceptedReference, events: tuple[RunTraceEvent, ...], acceptances: tuple[AcceptanceCommitted, ...]).

- initial.sequence == 0.
- Events form an exact 1..n prefix for this run with unique event IDs and nondecreasing BudgetUsage fields.
- A TerminalSnapshot payload, if present, is the last trace event. The returned completion envelope is separate.
- OperationObserved must cite an earlier DispatchIntent event ID with matching operation, and each intent has at most one observation. A pending/unobserved intent remains representable.
- Acceptance records form a complete chain from initial, with matching run, unique acceptance IDs, expected reference equal to the preceding accepted reference and sequence advancing once. They can exist even if an ordinary trace append later failed.
- accepted is derived from the final acceptance or initial.
- The trace event sequence and acceptance sequence are independent.

## Append and journal port

TraceAppended(event: RunTraceEvent), TraceNotAppended(event_id: RunEventId, explanation: str), TraceAppendUnknown(event_id: RunEventId, explanation: str). Validate types/nonblank explanations. TraceAppendResult is their union.

RunJournal( AcceptanceJournal, Protocol ) in core.ports.run_journal, without runtime_checkable:
- append(self, event: RunTraceEvent) -> TraceAppendResult
- snapshot(self) -> PortResult[RunJournalSnapshot]

Inherited commit/query signatures remain unchanged. Document that exact duplicate event IDs/events return the original acknowledgement, conflicting IDs or out-of-order sequences are refused, and a definite refusal changes no journal state. Unknown acknowledgement makes no claim about actual storage. The adapter owns one run and immutable initial reference. Its acceptance commit operation retains the existing atomic logical contract. snapshot returns a consistent view of trace and acceptance prefixes.

validate_trace_append_response(event, response) -> None checks types and exact event for TraceAppended, or matching ID for the other outcomes.

validate_run_journal_snapshot(run_id: RunId, initial: AcceptedReference, response: RunJournalSnapshot) -> None binds the exact run/initial reference. Coordinator initialization separately requires empty trace and acceptance prefixes for a new run.

No retry, dispatch, charging or storage implementation in this contract task.

## Completion report

RunReport(request: RunRequest, trace: RunJournalSnapshot, terminal_event: RunTraceEvent, terminal_append: TraceAppendResult, completion: ClockReading, final_run: RunRecord, authoritative: AcceptedReference | None, verified: AcceptedReference | None, unacknowledged_events: tuple[RunTraceEvent, ...]).

- Validate all types, exact request run/project/configuration/initial revision against final_run, and trace initial state == request.initial_state.
- terminal_event must carry TerminalSnapshot, have this run ID and match the request's project/configuration/initial revision. Its usage is the pre-finalization checkpoint.
- terminal_append must bind the exact terminal event using the same equality/identity rules as the port validator, implemented locally in records to avoid importing a port.
- final_run.usage.elapsed_seconds >= terminal_event.usage.elapsed_seconds, and all count fields equal that terminal checkpoint. Clock monotonic absolute origin is runtime-only; do not compare it directly to elapsed usage or impose ordering on UTC readings, which may be adjusted independently.
- final_run must be FINISHED. Reuse TerminalSnapshot(final_run, authoritative, verified) for final-state shape validation.
- A known authoritative reference must equal trace.accepted. A report with elapsed usage at or above the configured maximum cannot claim SUCCEEDED, including when finalization itself crossed the deadline.
- final_run usage is the observed post-write usage. It may differ in outcome/reason/explanation from the terminal snapshot because of finalization failure or overrun. If the terminal append is not TraceAppended, final_run must be FAILED/RECORDING_FAILED unless its FAILED reason is ACCEPTANCE_COMMIT_UNCERTAIN, ACCEPTED_STATE_INTEGRITY_FAILED, ACCEPTED_STATE_INTEGRITY_UNCERTAIN, RESTORATION_FAILED, RESTORATION_UNCERTAIN or REQUIRED_RECOVERY_NOT_PERFORMED (section 27 priority). It must never be SUCCEEDED.
- If acknowledged, require terminal_event to occur exactly in trace.events. For unknown acknowledgement, do not infer presence or absence in the supplied trace. Unacknowledged events preserve actual attempted records and must have unique IDs, this run ID and no conflicting contents with trace entries. These are attempts, not a claim of commitment.
- durability is a derived property returning exactly "non_durable" for this first controlled report.

The report may retain authoritative accepted edits after later recording failure. Do not infer selected state from whichever ordinary trace event happens to be last. Journal acceptance and integrity/reconciliation evidence remain separate.

## Owned files

- Add core/records/termination.py, core/records/run_trace.py, core/records/run_report.py and core/ports/run_journal.py under src/autonomous_mixing.
- Modify core/records/identifiers.py for RunEventId only and core/records/run.py for the explicit reason/explanation migration.
- Add tests/unit/test_run_recording_contracts.py and test_run_report_contracts.py. Migrate test_run_records.py and test_execution_records.py. Extend public signature tests if their pinned definitions are affected, preserving unrelated assertions.
- Update exact record and behavior inventories in tests/architecture/test_core_records.py and test_core_layers.py. Keep the graph acyclic. In particular, run may import termination but never run_trace, run_report or coordinator_runtime.
- Update overview in place and append research-log evidence/limits. Planning status updates only. Report additional required files before editing.

## Task

- [x] Record actual HEAD/status and read existing runtime/acceptance contracts and design. Run baseline checks.
- [x] Write and run meaningful red tests for reason migration, concrete payloads, operation pairing, pending intents, complete ordered prefixes, repeated IDs/conflicts, usage progression, independent acceptance chain and snapshot identity.
- [x] Implement lower-level reason/ID changes and trace/port records. Migrate existing constructors explicitly. Run focused and architecture tests.
- [x] Write red report tests for acknowledged terminal writes, failed/unknown writes, acceptance retained after recording failure, pre/post-write elapsed checkpoints, mismatched roots/state/run/configuration, truthful unknown integrity and unacknowledged event conflicts. Implement report validation.
- [x] Update docs and run full suite, Ruff lint/format, CLI help and uv lock --check with uv run --locked where available. Report omitted checks.
- [x] Review owned paths and return exact interfaces, red/green evidence, deliberate API changes, final checks and remaining storage/coordinator obligations. Stop uncommitted for independent review.
