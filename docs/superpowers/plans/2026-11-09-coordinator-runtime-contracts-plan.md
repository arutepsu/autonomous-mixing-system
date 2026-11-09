# Coordinator Runtime Contracts Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans sequentially.

**Goal:** Supply the remaining typed input and collaborators needed to assemble one controlled run.

**Architecture:** Data in core.records.coordinator_runtime, Protocols in core.ports.coordinator_runtime. Existing inner analysis, decision, rendering and integrity interfaces remain the reusable stage boundaries. Typed recording and terminal envelopes are the next portion of design Task 1, followed by coordinator integration.

**Tech Stack:** Existing Python, pytest, Ruff and uv.

**Spec:** docs/superpowers/specs/2026-11-09-controlled-coordinator-design.md, approved by the owner. Read the approved attempt contract sections 25–30 and slides 5–9 of the initial proposal for standing requirements.

## Scope and rationale

This is the runtime-contract portion of Task 1. Keep one Claude handoff reviewable. The ordered trace/journal and terminal contracts will follow in the next prompt before Task 2 coordinator code. Do not substitute arbitrary mappings, callables inside records or fake scientific methods to bridge missing collaborators.

The initial controlled run uses labelled responses. Actual MVP music comes from owner-provided FL Studio exports. Project/track roles are generic, and later project metadata ingestion and controller comparisons remain explicit milestones.

## Records

All new records are frozen, have required fields and preserve supplied objects. Use existing invariant helpers and ValueError for invalid supplied types/values.

1. DecisionRetryPolicy(reference: VersionRef, max_retries: int). require_count minimum zero. This is retries after the first decision call, per decision round. Only UNAVAILABLE+retryable failures qualify, subject to global time/call budgets. No retry behavior in this task.

2. ClockReading(monotonic_seconds: float, utc: datetime, cancelled: bool). Finite nonnegative monotonic value, UTC datetime and exact bool. Cross-reading monotonic progression belongs to the coordinator.

3. DiagnosisRequest(state: ProjectState, scope: AnalysisScope, observations: tuple[Observation, ...]). Validate scope/context-free known tracks and unique observation IDs. Validate every supplied observation source against this state's audio membership, retaining older-production members. Scope containment for coverage/candidates is checked when the diagnosis response arrives. Broader current observations remain permitted.

4. RunRenderRequest(plan: RenderPlan, before: StateIntegrityRequest, protected_artifacts: tuple[AudioArtifact, ...]). Types and unique protected artifact IDs. Require before.state project/revision to match the plan. Plan fixed_target_input, each current_stem and current mix ID must resolve to exactly matching before-state metadata (fixed input may be outside current audio and is found in before.required_artifacts). Require every before.required_artifacts member to occur with equal metadata in protected_artifacts. Additional retained artifacts are permitted. Protected metadata lists runtime source/output records, never hidden reference audio. Require candidate output IDs disjoint from all protected IDs. These are structural checks, not filesystem alias protection or action authorization.

5. RenderPreparationFailure(issue: ExecutionIssue, created_workspace: ExecutionWorkspace | None). Describes failure before inner rendering begins. created_workspace records a possibly retained allocation. No retry or cleanup claim. Actual unexpected partial-render failures use existing RenderFailure with FAILED/UNCERTAIN, not this preparation variant.

6. RunRenderOutcome = RenderedCandidate | RenderFailure | RenderPreparationFailure.

7. RunRequest(run_id: RunId, initial_state: ProjectState, configuration: RunConfiguration, policy: ActionAdmissionPolicy, renderer_capabilities: RendererCapabilities, processing_spec: ProcessingSpec, recipe: MixRecipe, stem_bindings: tuple[StemBinding, ...], mix_artifact_id: ArtifactId, measurements: tuple[ConditionMeasurement, ...], alignment: VersionRef, root_bindings: tuple[ArtifactRootBinding, ...], retry_policy: DecisionRetryPolicy).

RunRequest construction validates types, configuration.validate_against(initial_state.snapshot), policy.project_id == initial_state.project_id, one stem binding per recipe track with unique track/artifact IDs, recipe track set equals the snapshot track set, each bound stem is a current STEM of exactly that track, and selected mix is a current MIX whose contributing track set equals the recipe. Bindings may be in any order; the coordinator later arranges stems in recipe order.

Validate root_bindings by constructing StateIntegrityRequest(initial_state, root_bindings). Measurement bindings must be a nonempty tuple with unique condition IDs, exact condition coverage, selector and unit agreement with configuration criteria, including deferred INITIAL conditions. Reuse the existing VerificationAssessment validation logic by extracting a public validate_condition_measurements(criteria, measurements) -> None in core.records.verification_assessment, calling it both from its existing constructor and RunRequest. Move existing checks without changing their semantics. Add no temporary IDs or fake observations just to invoke a constructor.

RunRequest carries the renderer descriptor and policy; it cannot prove an implementation matches its descriptor. Baseline evidence, source bytes, actual alignment and run/journal identity binding remain runtime work.

## Protocols and response validation

Define in core.ports.coordinator_runtime, without runtime_checkable:

- DiagnosisSource.diagnose(self, request: DiagnosisRequest) -> PortResult[DiagnosisReport].
- RunRenderer.process(self, request: RunRenderRequest) -> RunRenderOutcome.
- RunClock.read(self) -> ClockReading.
- IdentitySource.next_token(self) -> str.

Identity tokens must be nonblank. The coordinator wraps them in existing typed identifiers and checks freshness against all identities already observed during that run. Concrete generators are later adapter work.

`validate_diagnosis_response(request: DiagnosisRequest, response: DiagnosisReport) -> None` checks argument types and calls validate_diagnosis_references against the supplied state/scope/observations.

`validate_run_render_response(request: RunRenderRequest, response: RunRenderOutcome) -> None` checks request/response types. For RenderedCandidate, call existing validate_rendered_candidate(request.plan, response). For RenderPreparationFailure with created_workspace, require equality to request.plan.workspace. Existing RenderFailure carries only its constrained status and issue; retain that contract. None of these checks performs I/O or selects/accepts a state.

Clock type/value validation is in ClockReading. No new clock or identity implementation, diagnosis detector, workspace allocation or outer rendering adapter in this task.

## Ownership

Read CLAUDE.md, applicable AGENTS.md and required references. Preserve existing work. No installs, dependency changes, commits or pushes.

- Add src/autonomous_mixing/core/records/coordinator_runtime.py and src/autonomous_mixing/core/ports/coordinator_runtime.py.
- Modify src/autonomous_mixing/core/records/verification_assessment.py only to extract/reuse validate_condition_measurements. Retain constructor signature and behavior.
- Add tests/unit/test_coordinator_runtime_contracts.py. Existing tests/unit/test_verification_assessment.py may add direct tests of the extracted validator; retain every original assertion.
- Update tests/architecture/test_core_records.py with the runtime public records, exact owner edges, and the extracted validator inventory entry. Update tests/architecture/test_core_layers.py with the Protocols and response validators. Keep all existing import restrictions and docstring-only initializers.
- Update overview in place and append research-log evidence and limits. Keep requirement statuses. Report additional required paths before editing.

Expected runtime record owner edges are actions, admission, artifacts, capabilities, diagnosis, identifiers, integrity, invariants, mix, observation, project, rendering, results (only if actually needed), run and verification_assessment. Use only the required subset. There must be no reverse edge from those owners into coordinator_runtime and no core.records import of ports/stages/loop.

## Task

- [x] Record actual HEAD/status, inspect existing signatures and read approved design. Run baseline checks.
- [x] Write and run meaningful red tests for record fields/types/immutability, exact run recipe/stem/mix bindings, required input roots, preserved object identities, protected input metadata and candidate-ID conflicts, clock validity, retries zero/count validation, diagnosis response scope/provenance and outer-render response identity/preparation workspace checks.
- [x] Extract the measurement binding validator unchanged and test its behavior against existing VerificationAssessment cases. Ensure no record cycle appears.
- [x] Implement the remaining contracts, update inventories and run focused, existing assessment, public-contract and architecture tests.
- [x] Update docs and run full suite, Ruff lint/format, CLI help and uv lock --check, using uv run --locked where available. Report omitted checks.
- [x] Review scope and return exact signatures, red/green evidence, checks, deviations and remaining trace/coordinator work. Leave uncommitted for Codex review.
