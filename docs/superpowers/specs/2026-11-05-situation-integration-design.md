# Situation integration — increment 2D

Status: proposed for owner review. No implementation authorized by this document alone.

## Intent and standing design

Implement the decision-input portion of the approved control-loop attempt contract, sections 11–15. Preserve the lifecycle OBSERVE → DIAGNOSE → PLAN/DECIDE → evidence/context gate → ACT → verification → STOP or ITERATE/ROLLBACK. Diagnostic uncertainty and action justification remain separate. Missing context is never guessed; a recorded context request ends the run and an answer starts a new run.

Diagnosis, action-capability and compact-feedback records already exist. This increment connects them to the immutable input for one decision call. It adds no detector, decision policy, provider, runnable loop or success claim.

## Approaches and chosen scope

The owner selected two increments: first enrich Situation while retaining current decision outcomes; later add explicit No Change, context-question requirements and structured proposal justification. Combining both would mix input-contract changes with output semantics and action gating. Embedding RunConfiguration or full attempts is excluded because it would create records dependencies back into decision records.

## Exact record extension

Retain the existing six required fields, in order: state, context, controller, observations, usage, limits. Append these six required fields, with no defaults:

| Field | Existing owner type |
| --- | --- |
| run_id | identifiers.RunId |
| scope | observation.AnalysisScope |
| criteria | verification.AcceptanceCriteria |
| diagnosis | diagnosis.DiagnosisReport |
| capabilities | capabilities.ActionCapabilityView |
| history | feedback.DecisionHistory |

Situation remains frozen in core.records.decision. Values are retained as supplied; no normalization, inference or construction of substitute records. This is an intentional constructor API break: update all repository callers and signature baselines. Do not provide compatibility defaults that fabricate missing information.

## Construction checks

1. Type-check all twelve fields, retaining the current unique-observation and context checks. Invalid supplied values follow the existing ValueError convention; omitted required arguments retain dataclass TypeError behavior.
2. Retain source validation of every current observation against state.audio and state.snapshot. Do not validate historical sources against the current checkpoint; retained historical observations may concern superseded artifacts.
3. Validate scope against the snapshot's known tracks.
4. For every target, improvement and guard selector, validate known tracks and containment within scope: track-set subset; closed endpoint comparison for time containment; when scope restricts frequency, an explicit contained selector band is required. A selector band is permitted when the run scope has no frequency restriction. Do not clamp or round.
5. Call validate_diagnosis_references(diagnosis, state=state, scope=scope, observations=observations). Empty coverage remains representable and does not establish that a mix has no problems.
6. Require capabilities.project_id and revision to match state, context_id and context_version to match context, and capabilities.scope == scope using existing record equality. Validate its scope against the snapshot. Do not regenerate the view or infer policy/renderer truth from it.
7. Call validate_decision_history(history, state=state, run_id=run_id, usage=usage). The first call has empty history and decision_calls == 1; subsequent calls require the complete prior-call prefix and its selected revision to equal the current revision. Usage includes the charge for this call. No budget dispatch or limit enforcement is added here.

Validation order beyond these groups is an implementation choice; do not pin incidental error messages or ordering in new tests unless existing public behavior requires it.

## Responsibility boundaries and explicit limits

Situation carries criteria, not AnalysisSpec or RenderSpec. Consequently this increment checks selector scope containment but does not claim metric-catalog membership, unit compatibility or render-Nyquist validation. Those existing RunConfiguration checks remain unchanged; the future assembler must validate fixed run configuration and bind the supplied criteria, context, controller, limits and capability references to that authoritative configuration.

The independent run_id prevents checking history against its own identifier tautologically. It does not prove a persisted run exists. History validation is structural, not permission to resume a terminated run. Context-request termination remains a future loop responsibility.

Do not require current observations to be wholly inside the run scope: broader measurements can supply context. Cited diagnosis scopes remain constrained through their existing validator. This increment does not add full analysis-setting compatibility checks.

Advertised capabilities are not authorization. Existing admit_action and preflight remain unchanged, including their documented enforcement gaps. Existing protection checks are not removed. No action is automatically accepted because its range appears in the view.

No full RunRecord, CorrectionAttempt, admission policy, raw transcript or recursive Situation history is embedded. No dependency from records.decision to records.run or records.admission is allowed. New records graph edges are only the required lower-level owners, and the records graph must remain acyclic. All package initializers stay docstring-only.

## Acceptance evidence

- Positive first-call and later-call constructions, preserving supplied object identities.
- Wrong types for each added field and omission of required fields.
- Wrong run/project/revision/context/scope bindings; invalid history count, chain and unknown selected revision.
- Every criteria collection exercises track/time/frequency containment, including exact endpoints and unrestricted frequency.
- Missing cited diagnosis evidence and stale current sources fail; unchanged older-production sources still pass when current members.
- Historical superseded observations remain valid history and cannot be silently moved into current observations.
- Empty coverage, empty gain_actions and empty first history remain explicit valid values without implying success.
- Existing behavior tests retain their assertions; migrate fixtures with explicit data and correct charged usage rather than weakening tests or validators.
- Records dependency/inventory checks, public-contract signature update, full suite, lint, formatting, CLI help and locked dependency check when available.

## Documentation and handoff

Update the overview in place and append a research-log entry describing the API break and its limits. No requirement is completed by this increment. Preserve earlier log entries. Leave implementation uncommitted for independent review; no new dependencies, provider work, execution changes or outcome variants.
