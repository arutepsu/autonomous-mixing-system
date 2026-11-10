# Control Loop Compact Feedback Implementation Plan (Increment 2C)

> **For agentic workers:** Use superpowers:executing-plans serially. Implement this increment only, test first, and stop uncommitted for Codex review. Preserve unrelated work; the owner commits after review.

**Goal:** Represent an immutable, complete prefix of prior decision-call feedback for later Situation input, including non-action decisions and failed calls without a valid DecisionId.

**Architecture:** Lower-level summary records live in `core.records.feedback`, never importing decision/admission/run records or the full journal. A new typed DecisionCallId identifies charged decision slots independently of successful parsing. Existing Action, Observation, VerificationResult, PortFailure and budget records supply typed data without a records cycle.

**Tech stack:** Existing Python >=3.11, stdlib, pytest, Ruff and uv; no dependencies added.

**Spec:** Standing lifecycle brief and approved `docs/superpowers/specs/2026-11-04-control-loop-attempt-contract.md`, particularly sections 13–15 and 25–30. Inspected clean HEAD `53ded0a`, after the reviewed capability view. Inspect later changes rather than reset them.

## Global constraints

- This is compact decision input, not the complete audit trail or its generator. Do not embed Situation, Decision, RunRecord, CorrectionAttempt, full ProjectState objects or provider transcripts inside feedback records.
- Current observations and historical observations remain separate. Do not validate historical artifacts against the current checkpoint or retag their production revisions.
- Describe actual outcomes; do not infer successful execution, acceptance or physical restoration from record construction.
- No new scientific metric, threshold, detector, calibrated confidence, comparison formula, retry policy or state transition is implemented.
- Frozen, required-field records and immutable tuples; existing invariant conventions; ValueError for invalid data. Existing records/signatures stay unchanged.
- Context requests end the run; answers start a new run. These records do not implement pause/resume or termination routing.
- Preserve the acyclic records graph and docstring-only initializers. No package exports, aliases, SDKs, arrays, I/O or mutable payload fields.

## Review focus

1. A call failure without a valid DecisionId must remain representable and distinct from a provider's explicit abstention.
2. Known failed/rejected actions, rollback and unresolved execution must not be summarized as acceptance.
3. Candidate revisions can become accepted; rejected candidates must not become the next before-state accidentally.
4. Historical observations may refer to superseded artifacts or the initial baseline; never apply current-state source membership to their past records.
5. History is an ordered complete prefix: no silent sorting/truncation, reused identities, decreasing usage or continuation from an unknown selected state.

## Ownership

- Modify `src/autonomous_mixing/core/records/identifiers.py`: add DecisionCallId using `_Identifier`; leave existing definitions unchanged.
- Create `src/autonomous_mixing/core/records/feedback.py`: the 14 public names below; private invariant helpers permitted.
- Create `tests/unit/test_decision_feedback.py`.
- Modify `tests/architecture/test_core_records.py`: add DecisionCallId to identifiers inventory; add feedback with allowed siblings exactly `identifiers`, `invariants`, `actions`, `observation`, `verification`, `results`, `budgets`, `project`, and its 14 public names. Do not relax existing edges/tests.
- Modify overview in place and append the research log, stating that projection generation, full trace, routing and Situation integration are still missing.
- Update this plan's checkboxes only.

No other source/test, behavior inventory, initializer, adapter, dependency file, accepted ADR or requirement status is owned. Stop/report a material conflict before expanding scope.

## Task 1: Feedback records and history reference check

### Exact enums

`ActionFeedbackDisposition(Enum)`:
- NOT_EXECUTED = `not_executed`
- REJECTED = `rejected`
- ACCEPTED = `accepted`
- ROLLED_BACK = `rolled_back`
- FAILED = `failed`
- UNRESOLVED = `unresolved`

`FeedbackStage(Enum)`:
- EVIDENCE_GATE = `evidence_gate`
- ADMISSION = `admission`
- PREFLIGHT = `preflight`
- PREPARATION = `preparation`
- RENDER = `render`
- VERIFICATION = `verification`
- RECOVERY = `recovery`
- ACCEPTANCE = `acceptance`
- RECORDING = `recording`

These classify summary data; they do not replace current RunState/AttemptDisposition or drive a loop.

### Exact frozen records and alias

All fields required, no defaults:

```python
ObservationFeedback(state_revision: Revision, observation: Observation)

ActionFeedback(
    action: Action,
    attempt_id: AttemptId | None,
    execution_id: ExecutionId | None,
    disposition: ActionFeedbackDisposition,
    stage: FeedbackStage,
    execution_status: ExecutionStatus | None,
    verification: VerificationResult | None,
    rollback_status: RollbackStatus | None,
    reason_code: str,
    explanation: str,
    observations: tuple[ObservationFeedback, ...],
)

NoChangeFeedback(explanation: str)
AbstentionFeedback(reason_code: str, explanation: str)
ContextRequestFeedback(questions: tuple[str, ...], explanation: str)
InvalidResponseFeedback(reason_code: str, explanation: str)
PortFailureFeedback(failure: PortFailure)
NotDispatchedFeedback(reason_code: str, explanation: str)

FeedbackOutcome = (
    ActionFeedback | NoChangeFeedback | AbstentionFeedback | ContextRequestFeedback
    | InvalidResponseFeedback | PortFailureFeedback | NotDispatchedFeedback
)

DecisionFeedback(
    call_id: DecisionCallId,
    sequence: int,
    run_id: RunId,
    project_id: ProjectId,
    before_revision: Revision,
    candidate_revision: Revision | None,
    selected_revision: Revision | None,
    decision_id: DecisionId | None,
    usage: BudgetUsage,
    outcome: FeedbackOutcome,
)

DecisionHistory(run_id: RunId, project_id: ProjectId, entries: tuple[DecisionFeedback, ...])
```

The 14 names are the two enums, ten records, FeedbackOutcome and validate_decision_history. Identifiers remain owned by identifiers.py.

### Basic record invariants

Type-check every field, including non-None optional values and the exact outcome union. Text is non-blank and preserved. Questions must be a nonempty tuple of distinct non-blank strings; no conversion from mutable lists. A ContextRequestFeedback is not a paused run. A NotDispatchedFeedback describes a charged decision slot that never invoked the provider, not a failure returned by that provider.

AbstentionFeedback.reason_code is a non-blank recorded code, but cannot equal `missing_context`: that needs ContextRequestFeedback with questions. Other recorded codes are descriptive; do not invent an alternative reason registry or coerce unknown codes into known ones.

ObservationFeedback type-checks its declared state revision and Observation. It does not prove membership in a historical checkpoint; the full trace must retain that evidence. In each ActionFeedback, `(state_revision, observation_id)` pairs are unique. Reusing an ObservationId at different state revisions is allowed only with identical Observation contents. Do not compute deltas or normalize historical values.

If a verification result is present, it must be ATTEMPT mode and execution_status must be CANDIDATE_READY. Every evidence ID in its checks must be present among the copied observations. This does not replace criteria-reference validation or prove the result was computed correctly. Initial-baseline observations may legitimately be included as well as before/candidate observations.

Execution ID and status are either both absent or both present. Rollback status requires an execution ID. Non-None attempt/execution IDs have their proper types. Every disposition except NOT_EXECUTED requires an AttemptId; NOT_EXECUTED may lack one when the attempt gate was never entered.

### ActionFeedback disposition invariants

| Disposition | Required shape |
| --- | --- |
| NOT_EXECUTED | Execution absent or NOT_STARTED; no verification or rollback. Stage may be EVIDENCE_GATE, ADMISSION, PREFLIGHT, PREPARATION, RENDER or RECORDING. |
| REJECTED | Stage EVIDENCE_GATE, ADMISSION or PREFLIGHT; execution absent or NOT_STARTED; no verification or rollback. This is a proposal rejection before candidate execution. |
| ACCEPTED | Stage ACCEPTANCE; CANDIDATE_READY; verification exists with ACCEPTED verdict; no rollback. Both intermediate and satisfied targets are allowed. |
| ROLLED_BACK | Stage RECOVERY; execution present; rollback VERIFIED. Verification is optional if execution permits it, and can even have ACCEPTED verdict if a later recording failure required restoration. |
| FAILED | Execution NOT_STARTED or FAILED; no verification or rollback. Stage PREPARATION, RENDER or RECORDING. Partial resources may remain; this is not restoration proof. |
| UNRESOLVED | Execution UNCERTAIN, or rollback FAILED/UNCERTAIN. Stage PREPARATION, RENDER, VERIFICATION, RECOVERY, ACCEPTANCE or RECORDING. Never infer successful restoration. |

All existing ExecutionStatus and RollbackStatus values remain unchanged. No PENDING feedback is added: this projection contains completed call summaries; pending intent/effect records belong in the full journal.

### DecisionFeedback invariants

- sequence is an integer >=1, bool rejected, and usage.decision_calls equals sequence. It counts the complete prefix's charged decision slots, not proof of successful remote calls.
- For ActionFeedback, decision_id is required, action.project_id equals project_id, and action.expected_revision equals before_revision.
- CANDIDATE_READY requires a candidate_revision different from before_revision. For all other execution statuses, candidate_revision must be None.
- ACCEPTED selects exactly candidate_revision. REJECTED, NOT_EXECUTED, FAILED and ROLLED_BACK select exactly before_revision. UNRESOLVED selects None or before_revision, never a tentative candidate. Selecting before during unresolved execution describes known accepted-state selection, not resolution of that execution; physical evidence remains in the full trace.
- NoChangeFeedback, AbstentionFeedback and ContextRequestFeedback require a DecisionId. InvalidResponseFeedback, PortFailureFeedback and NotDispatchedFeedback forbid it, because they do not describe a valid parsed decision. Partial/untrusted parsed IDs remain in the raw audit trail, not this valid-ID field.
- Non-action/failure/not-dispatched variants have candidate_revision None and selected_revision == before_revision.
- A full Observation is carried only inside ObservationFeedback, not by reusing current Situation.observations. No raw provider response, Situation, full state or run record is embedded.

### DecisionHistory invariants

- entries is a tuple of typed DecisionFeedback and may be empty. All entries match the history's project/run.
- Entries occur in exact sequence 1..len(entries); reject gaps, duplicates and reordering instead of sorting or silently renumbering.
- CallIds and all present DecisionIds, ActionIds, AttemptIds and ExecutionIds are unique within the history. Shared observation IDs are handled separately below.
- Each entry after the first has before_revision equal to the previous selected_revision. If the previous selected revision is None, no later entry is permitted. An unresolved last entry is representable in a report.
- Each new candidate revision is fresh relative to prior visible before/candidate/selected revisions and its own before revision. Accepted candidate revisions may naturally become a later before revision. Reserved identities not present here still require the full run identity ledger.
- Across entries, all counts and elapsed usage are nondecreasing. Do not clamp overruns. Exact accounting of attempt/render/recovery reservations and effects is the loop/journal's responsibility, not inferred from this summary.
- ObservationFeedback revisions must name a state visible by that entry: its before/candidate/selected reference or one seen in the earlier prefix, including the initial before reference. Ignore None when collecting known revisions. Do not accept a reference only introduced by a future entry.
- Across the full history, one ObservationId always denotes identical Observation contents. Identical reuse with different recorded state associations is valid; duplicate `(state_revision, observation_id)` copies within one ActionFeedback are not.
- This validates data consistency, not actual call execution, retry/termination policy, scientific evidence or physical restoration. The loop remains responsible for ending on a context request and the other approved terminal outcomes.

### Reference check for a future decision input

```python
def validate_decision_history(
    history: DecisionHistory,
    *,
    state: ProjectState,
    run_id: RunId,
    usage: BudgetUsage,
) -> None:
    ...
```

Type-check inputs, require matching run/project, and for nonempty history require the last selected_revision to be known and equal state.revision. Current usage.decision_calls must equal len(entries)+1: it includes the next decision's projected/acknowledged charge as specified by the parent design. Current usage in every dimension must be >= the last history entry's usage. Empty history is valid with one charged decision slot; do not enforce unrelated-counter defaults or limits here.

Return None. Do not call validate_observation_sources against this current state: historical evidence may cite valid artifacts superseded later. This function checks a prefix's linkage to a proposed next input, not full audit/termination correctness; it must not be used to bypass the loop's terminal-outcome checks or cause same-run continuation after a context request.

## Step 1: Failing payload and entry tests, then records

- [x] Read both parent specs, this plan, conventions, existing Action/Observation/VerificationResult/budget/result records and architecture checks. Record HEAD/status and preserve unrelated work.
- [x] Add metadata-only tests importing the missing records/identifier. Pin enums, union members, exact required fields, type/tuple/text/frozen invariants and every disposition/variant rule. Establish a red missing-module/name run before implementation.
- [x] Implement DecisionCallId and the payload/entry records; add the records module/identifier and only the names implemented in this step to architecture inventories. DecisionHistory and validate_decision_history join the inventory in Step 2, not as untested stubs. Run focused record tests and records architecture.
- [x] Key scenarios: port failure and invalid response with no DecisionId; explicit abstention versus missing-context questions; no-change versus zero gain; NOT_EXECUTED proposal with no AttemptId; partial failure never labelled accepted; ACCEPTED with unmet targets; ACCEPTED verification followed by verified rollback after a recording failure; UNRESOLVED known before selection versus unknown selection. Reject contradictory shapes.

## Step 2: Failing history/reference tests, then validation

- [x] Write tests for complete history and the missing reference function; establish red failures before implementation.
- [x] Cover an empty first-call history; a transient call failure followed by an accepted action and a later rejection; accepted intermediate state becoming the next before state; rollback preserving a previously accepted reference.
- [x] Reject reordered/gapped sequences, bool/zero sequence, wrong run/project, duplicate call/decision/action/attempt/execution IDs, stale chains, reused candidate revisions, decreasing counts/time and continuation after an unknown selected state. Preserve input ordering and objects.
- [x] Cover initial/before/candidate ObservationFeedback revisions. Reject future/unknown revisions and conflicting ObservationId contents; allow identical reuse from unchanged artifacts and repeated evidence in different states/calls. Do not impose current-checkpoint membership on past evidence.
- [x] Cover exact current charged decision count (len+1), wrong expected run/project, stale current revision, unknown final selection, current usage below history and old production revisions in historical artifacts. The helper returns None for valid links and raises ValueError for invalid data.
- [x] Implement history invariants and validate_decision_history with only allowed records/stdlib imports; append these two names to the records inventory so the final inventory is exactly the 14 specified names. Run focused and architecture tests.
- [x] Temporary mutations: remove accepted-selected-candidate equality; drop chain continuity; remove conflicting-observation-content protection. Each must fail a suitable test; restore byte-identically and rerun focused tests. No mutation tooling/dependency added.

## Step 3: Documentation, verification and handoff

- [x] Update overview in place and append log evidence. State this is a compact descriptive prefix only: no projection builder from the full trace, raw-provider capture, persistent journal, routing, retries, physical integrity verification or Situation integration exists yet. FR-06/FR-12/QR-03 remain incomplete.
- [x] Run:

```text
uv run --locked pytest tests/unit/test_decision_feedback.py -q
uv run --locked pytest tests/architecture tests/unit/test_public_contracts.py -q
uv run --locked pytest -q -rs
uv run --locked ruff check .
uv run --locked ruff format --check src tests
uv run --locked autonomous-mixing --help
uv lock --check
```

- [x] Inspect scope, unchanged old definitions/signatures, append-only log, package initializers and dependency files. Use existing .venv equivalents only if uv genuinely unavailable, reporting the missing lock check; do not install anything.
- [x] Stop uncommitted with exact paths/interfaces, red/green and restored-mutation evidence, observed checks/skips, deviations and gaps. Do not wire Situation, add No Change to actual Decision outcomes, generate projections or implement the next increment.

## After review

The next separately planned task integrates the reviewed diagnosis, action view and history into Situation and adds the actual No Change/context-request decision semantics. Keep that API change separate from this descriptive history slice.
