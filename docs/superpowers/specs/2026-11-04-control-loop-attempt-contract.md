# Control-loop attempt contract: state ownership and walkthrough

Date: 2026-11-04
Status: Tasks 1–4 approved by the owner on 2026-11-04. The first exact implementation plan and Claude prompt cover the budget gate only; product implementation has not started.
Standing requirements: `2026-11-04-control-loop-lifecycle-design.md` in this directory.
Plan: `../plans/2026-11-04-control-loop-contract-review.md`.

## 1. Scope and evidence

This document traces one correction through OBSERVE, DIAGNOSE, PLAN / DECIDE, ACT and EVALUATE, then STOP / ITERATE / ROLLBACK. It answers the state-ownership part of migration-plan section 5. Later tasks define exact diagnosis/history records, verifier computation, budgets and persistence. Nothing here claims those capabilities are implemented.

The walkthrough uses current source contracts: `Situation`, `Decision`, `DecisionResponse`, `ActionAdmissionRequest`, `ExecutionRequest`, `RenderPlan`, `RenderedCandidate`, `RenderFailure`, `ExecutionReceipt`, `VerificationResult`, `CorrectionAttempt` and `RunRecord`. Current functions and invariants were inspected in `core/records`, `core/ports`, `core/admission`, `core/verification`, `core/loop` and `adapters/rendering/gain_mix.py`.

The approach is the accepted ADR-027 design: a deterministic core orchestrates pure stages and injected ports. The AI decision component proposes; adapter implementations perform file/audio/provider operations. The walkthrough does not introduce a second orchestration framework or assign run control to the LLM.

## 2. The state ledger

Names in this table are explanatory variables, not proposed public Python types or new fields.

| Name | Meaning | Who can change the selected reference? |
| --- | --- | --- |
| `initial` | Complete starting `ProjectState`, retained for initial-baseline comparisons and provenance. Being the starting reference does not establish that its mix meets the target criteria. | Fixed for this run. |
| `accepted` | Complete last accepted `ProjectState`. Initially it is the starting reference; later it is a candidate that passed the required checks and acceptance transition. | Only the deterministic loop, after verification and the required history/commit boundary. |
| `before` | The saved `accepted` state at the start of this correction. It supplies the attempt baseline and rollback reference. | Fixed for this attempt. |
| `candidate` | A newly derived complete state describing the proposed change and newly rendered artifacts. It is tentative even after rendering and structural validation succeed. | The loop derives it from the saved state, action and execution receipt. It never mutates `before`. |
| `fixed inputs` | Per-track input artifacts retained in `ProjectProcessingState`, from which absolute gain settings are rendered. | Carried over unchanged in the supported gain operation. |

Starting-state integrity and analysis preconditions must be established before a run claims successful input verification. Existing record construction alone does not prove file integrity. The exact run-start preparation contract remains part of the integration design.

State selection and stored-object immutability are separate from physical artifact integrity and durable publication. A Python reference assignment alone does not establish file restoration or persistence.

### Invariants

- A decision, action, preflight request and rendering plan refer to `before`'s project and revision, not unconditionally to `initial`.
- Candidate artifacts have fresh identities and a candidate revision distinct from locally known revisions. Run-wide identity uniqueness must also account for rejected/failed attempts, not just the current accepted state's local inventory.
- Superseded artifact IDs are explicit. For the existing gain renderer, replacements are exactly the selected target stem and full mix; preflight rejects additional affected dependencies it cannot handle.
- Unchanged artifacts retain their original production revisions. Checkpoint membership describes the selected state; it never retags artifact provenance.
- Current non-target stems remain unchanged. A new absolute target gain is applied to the fixed input, not reapplied to an already attenuated current target stem.
- Rejected or failed outputs never enter the accepted checkpoint. Retaining partial files is not accepting them.
- Hidden references and defect-injection ground truth are absent from every runtime input and trace.

## 3. Stage-by-stage ownership

All proposed resource charging below is design intent for Task 4, not behavior of existing records. Physical work remains in adapters. Trace contents listed here are requirements to design, not fields already present on `CorrectionAttempt`.

| Stage | Input and owner | Output / handoff | Invariants and side effects | Resource and trace obligation |
| --- | --- | --- | --- | --- |
| OBSERVE | Loop selects `accepted`; calls `AudioAnalyzer.analyze(AnalysisRequest(state, scope, specification))`. | `PortResult[AnalysisResponse]`, checked with `validate_analysis_response`. Save the exact request and returned observations. | Measurements must refer to the selected state's actual member artifacts, scope and analysis specification. Audio reads occur in the analyzer adapter, not the loop. | Record call, response/failure and elapsed time. Analysis retry/count policy is unresolved. |
| DIAGNOSE | Pure diagnosis stage consumes current measurements and the relevant context. | Evidence-linked problem candidates and uncertainty for decision input. Exact records are Task 2. | No guessed diagnosis or invented scientific thresholds. Absence of a detector is not evidence of no problem. | Record candidate provenance and any unavailable evidence. No provider/file calls are implied by this conceptual stage. |
| PLAN / DECIDE | Loop forms the exact `Situation`; calls `DecisionMaker.decide`. | `PortResult[DecisionResponse]`; response is checked with `validate_decision_response`. | Capture supplied context, observations, history and candidates; current `Situation` lacks the latter two. A proposal is not authorization. | Check and charge decision-call allowance before the call; capture actual request and outcome, including invalid responses. Provider retry/provenance details are Task 4. |
| Evidence/context gate | Proposed decision plus its evidence and supplied context. Loop coordinates pure rules. | A justified proposal continues; otherwise No Change / Abstain / Request Context is recorded. | Diagnostic confidence and action justification remain separate. No Change does not establish success without deterministic verification. | Record the reason even if there is no action or `CorrectionAttempt`. Exact outcomes are Task 2. |
| ACT: admission | `admit_action(ActionAdmissionRequest(situation, decision, policy))`. | `AdmittedAction` or `RejectedAction`. | Reject protected tracks, unpermitted gains and bounds violations before renderer invocation. No file operations. | Record policy version and result. Rejections still consume the preceding decision call. |
| ACT: preflight | Full `ExecutionRequest` containing the saved situation, decision, recipe/bindings, logical workspace and fresh candidate identity. | `MetadataReady(RenderPlan)` or `PreflightRejected`. | `preflight_execution` reruns admission. Metadata agreement is not proof of physical alignment, file integrity or a race-free authorization. | Record request and first rejection reason. Never call the renderer after a preflight rejection. |
| ACT: resources / render | Fresh logical assignment, complete protected inventory, explicit roots and `RenderPlan`. Adapter-side preparation supplies a suitable renderer context. | `Renderer.process(plan)` returns `RenderedCandidate` or `RenderFailure`; unexpected exceptions follow the established exception policy. | Actual WAV checks/writes stay in adapters. Workspace creation is a side effect and must be tracked even if no WAV is produced. Resource binding is an open integration contract, detailed below. | Check render/recovery allowances before effects. Record invocation intent, observed effects, successful outputs, failures and retained files. Exact charging points are Task 4. |
| ACT: reconcile candidate | Checked renderer response, `before` and action. Loop calls `validate_rendered_candidate`, forms an execution receipt, then `derive_candidate_project_state`. | Linked `ExecutionReceipt` and tentative `ProjectState`. | A complete candidate requires valid outputs and references. Run `validate_gain_execution`; inconsistent output is not accepted or silently repaired. This metadata step does not write audio. | Record full renderer evidence as well as the receipt; the receipt alone does not contain it. |
| EVALUATE | Analyze `candidate` through the analysis port. Pure verification consumes those observations, `before` observations, initial-baseline observations where required, and fixed `AcceptanceCriteria`. | `VerificationResult`, checked with `validate_verification_references`. | Compare compatible observations. That existing function validates result consistency; it does not calculate the verdict. Runtime verification never reads hidden research data. | Capture exact analysis/verification inputs and checks. Failed re-analysis cannot produce an accepted result. |
| STOP / ITERATE / ROLLBACK | Verified result and saved state/evidence. The loop owns the disposition. | Accept complete success; accept qualifying intermediate progress; reject and restore/verify; or terminate with a known failure/uncertainty. | Only the loop promotes a candidate. Restoration claims need physical integrity and accepted-reference evidence. | Append the disposition, selected state, counters and reason. Durable ordering and repeat/stop rules are Tasks 3–4. |

Admission and preflight remain separate responsibilities even though preflight defensively reruns admission. Do not charge two action executions for this pure recheck. An action proposal or unused ID reservation is not a render call. The precise meaning of the attempt counter remains to be fixed before implementation.

## 4. One successful correction

This is a structural example, not a research example or a justified mixing prescription. Assume an editable track `T` with an explicit fixed input and two or more aligned current stems. The one proposed operation is an allowed absolute gain setting; no default gain, instrument role or threshold is assumed.

1. Save `before = accepted`; retain `initial` and the fixed inputs independently.
2. Observe `before`, validate its observations and diagnose evidence-linked candidates. Build and capture the exact `Situation`.
3. Obtain and validate a decision proposing one `SetTrackGain(T, requested_db)`. Sufficient justification and context are required.
4. Admit the proposal. Reserve fresh execution/candidate/checkpoint identities through the run's identity mechanism. Populate the explicit recipe and bindings. The physical root is not inferred from the working directory.
5. Preflight succeeds. After required resource preparation, invoke a renderer bound to this execution and `before`.
6. The concrete gain renderer checks the fixed target input and current non-target stems. It renders the target from its fixed input at the requested absolute gain, sums the full mix in recipe order, and writes new target and mix files. It does not reopen the obsolete target or old mix merely to sum them.
7. Validate the `RenderedCandidate` against the plan. The loop constructs a `CANDIDATE_READY` receipt linked to this action and `before`, with its two new artifact records and explicit superseded IDs. Retain its `RenderEvidence` separately in the trace.
8. Derive `candidate` with the supplied checkpoint ID and timestamp, carrying fixed inputs and unrelated settings forward. It remains tentative.
9. Analyze `candidate`, compute verification checks using the configured baselines, and validate the recorded result.
10. If targets are satisfied and guards pass, prepare an accepted disposition and select `candidate` at the defined acceptance/recording boundary. If only qualifying intermediate progress is accepted, this same promotion occurs and bounded iteration may continue. Neither a renderer nor the LLM performs this promotion.

The acceptance/recording boundary is a required later decision: a durable run must not claim acceptance or replayability if recording/publication fails. This walkthrough does not invent an atomic commit or crash-recovery protocol.

### The next attempt

After acceptance, the next `before` is that accepted candidate, not the initial state. The new request carries that revision and current stem/mix membership. A second absolute gain setting for `T` still uses the original fixed input. Non-target current stems may include outputs accepted in earlier attempts. An `INITIAL` delta guard still references initial-baseline observations; an `ATTEMPT` guard references the current `before`.

After rejection, the next `before` remains the saved last accepted state, subject to restoration/integrity checks. History includes the rejection so the decision maker does not see a fictitious first attempt. Fresh identities are required even though the accepted revision has not advanced.

## 5. Rejected action walkthrough

1. Observation and decision have already happened; their input/output and resource use remain part of the run history.
2. Admission rejects the proposal, or preflight rejects its metadata after the pure admission recheck.
3. The loop records the rejection and leaves `accepted` unchanged. No renderer call occurs. With resource preparation ordered after preflight, no output directory or candidate file need be created for this path.
4. No rollback is claimed, because no candidate execution was attempted. A rejected proposal need not be forced into `CorrectionAttempt`, which requires an action-oriented identity structure; the run needs an iteration/decision trace that also represents abstentions and rejections.
5. Whether another decision is allowed is a bounded run policy, to be resolved in Task 4. A rejection does not replenish any allowance.

## 6. Regression walkthrough

1. Rendering and candidate derivation succeed; `candidate` remains tentative and `accepted == before`.
2. Re-analysis reveals a failed guard or an unacceptable target/improvement result. Do not accept the candidate on the basis of a persuasive rationale or a better single metric.
3. Preserve the rejected outputs and their evidence as unaccepted artifacts; no cleanup is implied.
4. Restore/select the saved accepted reference and establish that its complete saved state and required artifacts remain intact through an adapter-facing integrity/recovery operation. A matching `ProjectState` object alone is insufficient.
5. Only supported restoration evidence can produce `RollbackReceipt(VERIFIED, restored_state=before, ...)` and `AttemptDisposition.ROLLED_BACK`. Failed or uncertain restoration remains explicitly unresolved and cannot be reported as successful rollback.
6. Return the verified last accepted state to the outer loop; continuation after rejection is constrained by history, budgets and later run policy.

No inverse gain operation is needed merely to reconstruct an old file in this non-destructive renderer. That does not eliminate accepted-reference selection or file-integrity obligations.

## 7. Partial-output failure walkthrough

1. The renderer creates `target.wav` but fails while producing `mix.wav`. It retains files it created.
2. The current `GainMixProcessor` can return `RenderFailure(FAILED, issue)` with retained paths in human-readable issue text. It does not return a valid `RenderedCandidate`. Unexpected exceptions can also occur after creation and may not report every created file.
3. A known failed execution receipt must not invent a candidate revision, registered candidate artifacts or superseded IDs: those fields are forbidden for `ExecutionStatus.FAILED`. Retained partial files therefore need separate trace/resource evidence, not a forged successful candidate.
4. The accepted reference has not been promoted. `AttemptDisposition.FAILED` describes a known unsuccessful execution and does not certify restoration or absence of leftover files. It is usable only with the required execution receipt.
5. Unexpected post-invocation failure must be reconciled conservatively. Do not infer no side effects or blindly retry in the same workspace. An uncertain execution requires `UNCERTAIN`/unresolved evidence until the run can establish what happened.
6. Execution uncertainty and final accepted-state integrity are distinct. If the saved accepted state can be verified, the run may know that state remains usable while still recording an unresolved execution. If it cannot establish the final state, report uncertainty rather than success. Exact terminal mapping is Task 4.

**Contract gap:** current failure records do not provide a complete structured retained-file inventory. A future trace must record observed paths, hashes where actually known, workspace ownership and uncertainty without parsing free-form messages or fabricating evidence.

## 8. Existing records: proposed disposition

| Contract | Recommendation for first loop | Reason |
| --- | --- | --- |
| `AnalysisRequest` / `AnalysisResponse` | Keep. | They describe measurement of one state and its observations. |
| `Situation` | Keep name; extend later with specified history/candidates. | The exact runtime-visible decision input is a separate provenance requirement. |
| `DecisionResponse` | Keep initially. | Removing it does not close a demonstrated walkthrough gap; consistency checking already expects it. Revisit only with a concrete payload need. |
| Admission request and `ExecutionRequest` | Keep initially. | Admission expresses permission; execution adds identities, bindings, recipe, workspace, configuration and candidate assignment. Duplicated values must agree, not silently diverge. |
| `RenderedCandidate` and `ExecutionReceipt` | Keep both. | Renderer output includes file-derived evidence; the receipt links execution, action and replaced state. They are not interchangeable. |
| `RollbackReceipt` | Keep its meaning. | It distinguishes a verified restoration claim from failure/uncertainty. Its constructor/reference validator is not physical proof. |
| `CorrectionAttempt` | Keep as an action-oriented result record; augment enclosing trace. | It does not hold all observations, actual `Situation`, raw provider data, rejections or retained-file evidence. |
| `RunRecord` / configuration / budgets | Keep as groundwork. | Enforcement, enumerated termination reasons and complete history/persistence remain to be defined. |

These are proposed design recommendations for review, not approved signature changes or a freeze on later necessary changes.

## 9. Ownership gaps that must be closed

### Execution resources and identities

The loop must associate fresh logical execution/candidate/checkpoint identities with each attempt. ID-generation mechanics must be injected or otherwise explicitly defined without depending on import order or filesystem scans. Pure consistency checks cannot certify run-wide uniqueness by themselves.

Physical resource preparation belongs to the rendering adapter boundary, composed by the driver. The current `GainMixProcessor` is bound to one `AllocatedWorkspace`; it is not a ready-made renderer for arbitrary attempts. `WorkspaceAllocationRequest` additionally needs the complete state and a protected inventory covering current audio, fixed baselines and retained sources/outputs where required. `RenderPlan` alone is not that inventory.

Recommendation: define an explicit per-attempt preparation/rendering integration that receives the necessary complete state/protection context, allocates fresh resources and invokes the existing bound processor. A preparation port versus a revised run-level rendering request remains an API-design choice; do not smuggle adapter instances, mutable handles, or `AllocatedWorkspace` into core records. The driver must not become an alternative owner of the lifecycle while solving composition.

### Trace and durable selection

Record decision calls even when they produce no action. Record render intent before side effects at the eventual persistence boundary, then actual observed outcomes. Capture complete successful render evidence and partial-output uncertainty. Trace selection of `accepted` explicitly; durable publication and crash recovery need a defined protocol before a durable integration can claim completion of FR-12/QR-03.

### Verification and recovery effects

Pure verification can compare observations; it cannot inspect the filesystem. Integrity/recovery must be available through a defined adapter boundary. The current three ports do not yet express complete accepted-reference restoration and per-attempt resource preparation. Test doubles must model these guarantees honestly; a fake verified receipt is not evidence of production restoration.

## 10. Decisions for the next tasks

Owner-visible choices:
- Request Context: end the current run with a recorded request, or pause/resume it? No choice is inferred from approval of this planning exercise.
- Scope of first runnable milestone: the plan recommends controlled ports and deterministic lifecycle scenarios, with real provider/audio integration and durable recovery reviewed separately. These scenarios do not complete the scientific requirements.

Technical recommendations to review, then settle in later tasks:
- Keep current wrappers/requests/receipts initially as described above; design missing history/evidence rather than merging by similar names.
- Choose the resource-preparation boundary and its complete protection context before connecting repeated real renders.
- Define No Change and evidence-gate outcomes independently of numeric confidence calibration.
- Define exact resource charging, retries, terminal reasons and recovery at exhausted budgets; the renderer must not be invoked on an unfunded path.
- Specify verifier compatibility and unavailable-evidence policy before a successful end-to-end acceptance test can stand for real verdict computation.

Task 1 review completed by owner approval on 2026-11-04. The following sections are the Task 2 design; sections 1–10 establish its approved starting point. The context-request choice listed above is resolved explicitly in section 15.

## 11. Task 2: the decision input

OBSERVE / DIAGNOSE / PLAN must form one immutable, runtime-visible input for one decision call. Retain the name `Situation`. Its complete core input and the adapter's actual serialized provider request are different provenance artifacts; both must eventually be captured. Capturing a Python object alone does not capture a prompt or prove durable persistence.

The table defines required information, not final field names or Python signatures. Existing fields remain the starting point; the additional information is a proposed contract extension to be specified precisely in the implementation plan after Tasks 3–4.

| Information in `Situation` | Meaning and source | Status |
| --- | --- | --- |
| Current accepted `ProjectState` | Exact project, revision, audio membership and processing settings chosen by the loop. Candidate states are not exposed as accepted. | Existing `state`. |
| Versioned musical context | Explicit roles, preferences and hard protections, with available assertion provenance. An absent role or preference remains unknown. | Existing `context`; richer hard-constraint types remain D6. |
| Decision-maker identity | The configured controller/model condition, checked against the response. | Existing `controller`. |
| Current observations | Measurements or explicit `Unavailable` results tied to this state's artifacts and analysis specification. | Existing `observations`; source/reference checks retained. |
| Resource use and limits | A consistent snapshot at a declared capture point; remaining allowance is derived rather than guessed. | Existing `usage` / `limits`; charge timing belongs to Task 4. |
| Run scope and declared objectives | Which tracks/regions are in scope and which predeclared target/guard criteria govern this run. These are runtime criteria, not hidden ground truth. | Additional explicit input; avoid silently relying on prompt defaults. |
| Diagnosis report | Evidence-linked candidates plus coverage/method availability, including explicit uncertainty. | Additional explicit input; see section 12. |
| Permitted action view | Actions actually supported by the configured renderer, intersected with current policy, protections and scope, including applicable parameter bounds. | Additional read-only input derived from authoritative configuration. It is not execution permission. |
| Previous decision/action feedback | Ordered compact summaries of earlier calls and attempts, including rejected and failed ones, with their actual state revisions and outcomes. | Additional input; see section 13. |

The action view must not advertise EQ, dynamics or stereo adjustment while only gain is supported. Including policy information in a decision input does not replace admission; the core rechecks the authoritative policy before rendering. The loop, not the provider, assembles these inputs.

### Input integrity

- Current observation IDs are unique and source metadata must match current checkpoint members exactly. An older artifact production revision is legitimate when that unchanged artifact remains a member of the current state.
- Earlier observations cannot be injected into the current observation collection merely because they concern the same track. An observation whose actual sources were superseded is invalid for the current state. Reuse for unchanged retained sources is not forbidden by its age or their production revision alone: it needs exact source membership/metadata and compatible measurement settings, with reuse/provenance recorded. Task 3 defines the complete compatibility rule. Historical feedback retains its original observation/state identities.
- Scope, objectives, policy view, candidates and context must agree with the current project/revision and fixed run configuration. Updated context is not silently inserted into an existing run.
- Missing measurements remain `Unavailable`, not zero or an invented estimate. Missing roles are not inferred from track names, file paths or general genre expectations.
- Do not require every possible context field for every action. Identify the particular absent information that prevents justification of this action.
- Ordinary runtime artifact metadata is allowed; hidden-reference audio, injection labels/parameters, credentials and hidden-data root references are excluded. An adapter must not obtain extra context by reading undeclared files.

### Dependency constraint

Do not embed `ActionAdmissionPolicy` directly in `Situation` without redesigning ownership: `core.records.admission` already imports `Decision` and `Situation`, so importing that module back into `core.records.decision` would create a forbidden records cycle. Likewise, do not embed a full `RunRecord` or `CorrectionAttempt` history into `Situation`: run records already depend on decisions. Shared action-view and feedback records must have lower-level, acyclic ownership. Their exact modules are to be chosen with the final field schema and checked against the records graph.

## 12. DIAGNOSE: candidates and evidence strength

The pure diagnosis stage interprets documented deterministic measurements into plausible problem candidates. It does not read samples, call the provider or use hidden reference data. The decision maker can reason about these candidates, but its interpretation does not retroactively establish a measured fact or an approved detector method.

Every candidate must carry the following information to satisfy FR-05:
- A unique identity and a declared problem family/type.
- The affected tracks and analysis region/scope.
- The supporting observation IDs, all available in the supplied current measurement context.
- The detection method and its version.
- An explicit account of evidence strength, uncertainty, limitations and any missing information.

Candidate fields and identifiers are a schema to design; no numerical confidence threshold is established here. D11 remains open for the scientifically justified uncertainty representation. The first contract must accommodate qualitative or uncalibrated statements explicitly labelled as such. A model's “90% confident” does not become a calibrated diagnostic probability.

### Coverage is separate from candidates

A diagnosis report must distinguish at least these situations semantically:

| Situation | Meaning | Consequence |
| --- | --- | --- |
| Method ran over its declared scope and returned candidates | Specific hypotheses have supporting measurements. | The decision maker assesses whether an available action is justified. |
| Method ran and returned no candidates | No candidate met that method's documented rule in that scope. | Does not prove the whole mix is problem-free or replace target/guard verification. |
| Method unavailable / unsupported | This problem family or region was not assessed. | Must not be encoded as a successful empty report. |
| Evidence incomplete | Missing or incompatible observations prevent a stated conclusion. | Identify what is missing; do not invent a negative result. |

Reports may have mixed coverage across families or regions. A bounded initial implementation can use labelled test-double diagnosis reports to test the loop, but must not claim production masking/buildup/phase detection. Implementing real methods still requires the relevant literature/method decisions and roadmap work (ADR-006, FR-13).

### Diagnostic confidence versus action justification

Diagnostic uncertainty concerns whether a candidate problem is plausible. Action justification concerns why this particular permitted operation is appropriate, given producer intent, prior feedback, capability and expected effects. These are recorded independently.

For a proposed correction, require a link to the target candidate(s), supporting measurement references, the expected effect and material assumptions/limitations. The existing `Decision.rationale` and `ProposeAction.expected_effect` are useful but do not yet supply structured candidate references or a separate uncertainty record.

The deterministic gate checks references, available evidence, declared missing-context requirements, supported operations and hard constraints. It cannot prove that an arbitrary LLM rationale is musically correct. A well-formed proposal and self-reported confidence are not perceptual validation or automatic execution permission.

## 13. Feedback: decision history versus audit trail

### Complete audit trail

For each call, preserve the exact `Situation`, provider-facing payload where applicable, raw response or failure, parsed decision, response validation result and subsequent disposition. Calls that fail, abstain or are rejected must remain visible even when no valid `DecisionId` or action exists. Use a call/sequence identity independent of successful decision parsing; exact types are Task 4.

For actions, additionally preserve admission/preflight outcomes, execution identities and plans, renderer evidence, candidate measurements, verification checks, selected accepted state, restoration evidence and observed retained resources. This is broader than `CorrectionAttempt`. Persistence and replay guarantees are not completed by specifying these contents.

### Compact feedback supplied to the next call

The loop derives an immutable, ordered projection of the completed prior entries. Include:
- The call/attempt order and identities.
- Before, candidate if any, and selected state revisions.
- The actual proposed action and its absolute parameter value, or No Change / Abstain / Request Context.
- The reason for admission/preflight rejection or port failure where present.
- Known execution status, acceptance/rollback disposition and verification feedback, including relevant compatible before/after values or explicit missing evidence.
- Context-request questions and unresolved execution/restoration uncertainty where present.

Do not recursively embed earlier `Situation` objects, a full run object or raw transcripts in this projection. The full audit trail retains those originals separately. Do not report a failed action as an accepted edit or a partial file as a successful candidate. The first decision has an explicitly empty history.

The intended initial policy is to provide compact summaries of every completed prior call in the bounded run. If a provider cannot accept the resulting request, record that failure or use an explicitly configured, versioned reduction policy that records exactly what was supplied. Do not silently truncate history or ask the LLM to invent feedback. Cross-run history is not imported automatically; any later continuation support needs an explicit provenance policy.

## 14. PLAN / DECIDE outcomes and routing

Retain `DecisionResponse` around one `Decision`. Preserve a structured proposal, explicit non-action outcomes and a reason. The following separation is proposed; exact types and signature changes must be specified before implementation.

| Outcome | Meaning | Route |
| --- | --- | --- |
| Propose action | One supported bounded operation, linked to evidence and target candidates, with expected effect and action justification. | Validate response and evidence/context gate, then admission and preflight. |
| No Change | Deliberate recommendation to leave the current accepted state as it is. It is not a failed render, a zero-valued gain action or insufficient evidence. | No rendering. The loop reports success only if deterministic target/guard criteria establish it; otherwise record a distinct no-action, non-success disposition. Exact terminal reason is Task 4. |
| Abstain | The decision maker cannot justify an intervention because of insufficient evidence, conflicting constraints or no justified available action. | No rendering; record the stated reason and limitations. It must not become a success claim. |
| Request Context | Specific supplied musical context is missing and needed to justify an action. | Recorded abstention with questions; end the run under the owner's decision in section 15. |
| Malformed response / provider failure | No valid decision was produced. | Record a call-level validation/failure result. Retry only under a later explicit bounded policy; do not fabricate an Abstain/No Change that the provider never returned. |

### Proposed representation

- Retain `ProposeAction` and `Abstain`. Extend the decision outcome union with a distinct No Change variant, rather than turning No Change into a gain operation or using insufficient-evidence reasons for it. The variant's final name/signature is not implementation authorization.
- Represent Request Context using the existing `Abstain` with `AbstentionReason.MISSING_CONTEXT` and explicit `requested_context`, rather than inventing a separate paused-run mechanism.
- A missing-context outcome must include at least one actionable, non-blank question identifying the missing preference, priority or role and why it matters. Current validation only checks strings; these stronger semantics are a proposed contract change.
- Retain current non-action and failure reasons. The run records the provider's actual decision and the loop's routing/terminal reason separately; it does not rewrite a No Change response into a different provider response.
- If targets are already satisfied with applicable guards passing, the loop may terminate on deterministic initial verification without spending a decision call. This avoids treating a model's No Change as the source of success. Exact initial-assessment behavior is Task 3.

Admission currently returns NOT_AN_ACTION for abstentions; the loop should route valid non-action outcomes directly rather than presenting them as attempted tool executions. Defensive direct admission of a non-action can still reject it. Existing public-contract tests must be deliberately updated if a new outcome is added.

## 15. Request Context: owner decision

**Approved by the owner on 2026-11-04:** end the run with a recorded context request; start a new run with updated context. Do not pause/resume the same run in the first version.

Required behavior:
1. Capture the exact missing-context outcome, its questions and the input that led to it.
2. Invoke no renderer for that decision. Finish the run as an abstention, with a specific missing-context reason and the actual final-state disposition; exact reason enums are Task 4.
3. Keep the last accepted state. If earlier edits were accepted, do not mislabel this as an unchanged initial project or automatically discard those edits. Any unresolved integrity issue still requires its own truthful final-state reporting.
4. Present the questions through the driver. The LLM does not invent answers, launch a background wait or mutate context in the completed run.
5. Once the user supplies context, a new run gets a new `RunId`, an updated context version and fresh input validation/observation. Its selected starting state and any predecessor linkage must be recorded explicitly. Updating context does not require inventing a new audio revision if the audio/settings did not change.
6. Preserve the earlier run and its original context/requests unchanged. No automatic cross-run history merge or restart is implied.

## 16. Proposed conformance scenarios

These are design acceptance cases to turn into tests in the implementation plan; they are not tests already executed.

| Input / condition | Required result |
| --- | --- |
| First decision | Current observations, explicit supplied context, supported-action view and empty prior history. |
| Decision after a failed or rejected proposal | Feedback includes that event and its charged resource use; state still points to the last accepted reference. |
| Accepted intermediate correction | Next input uses the new accepted state, while history retains the actual before/after evidence and fixed-input provenance. |
| Observation citing a superseded artifact inserted among current observations | Reject the invalid source/state association. An earlier observation from unchanged retained sources is assessed for compatibility, not rejected merely because it is older. |
| Diagnosis unavailable versus assessed-empty | Distinct reports; neither automatically establishes mix sufficiency. |
| Strong diagnostic evidence but missing artistic priority | Request that specific context; no inferred track role and no rendering. |
| Gain-only renderer, proposal for EQ/dynamics/stereo | Never execute unsupported operations; the provider input must advertise gain-only capabilities. |
| No Change with unmet or unknown targets | No rendering and no success based only on the decision. |
| Missing-context response without a question | Invalid response under the proposed stronger semantics; do not wait silently or invent a question. |
| Context request after an accepted edit | End with the request and retained accepted edits; a later reply starts a new run. |
| Invalid provider response or service failure | Preserve raw outcome and call identity without inventing a successful decision or correction attempt. |
| Provider input exceeds its supported size | Explicit failure or recorded configured reduction; no silent history truncation. |
| Hidden-reference fields, undeclared hidden paths or credentials | Excluded from runtime inputs, summaries and persisted provider exchanges. |

## 17. Task 2 decisions and requirement trace

**Owner-confirmed:** source lifecycle; deterministic orchestration; Task 1 ownership walkthrough; end-on-context-request followed by a new run.

**Task 2 design accepted on 2026-11-04:** the additional `Situation` information, acyclic ownership for its feedback/action-view records, explicit diagnosis coverage, a distinct No Change variant, the compact complete prior-call history policy, and the semantic evidence/context gate. These still need exact record schemas in the final implementation specification; D11 calibration/method choices are not resolved by accepting the information requirements.

| Requirement | Design contribution | Remaining work |
| --- | --- | --- |
| FR-05 | Evidence-linked candidates, method/version, scope, uncertainty and coverage. | Exact uncertainty schema, scientifically justified methods, implementation and tests. |
| FR-06 | Measurements, intent, candidates, history and explicit proposal/non-action outcomes in the decision contract. | New immutable records, response checks, loop routing and provider integration. |
| FR-07 / FR-11 | Explicit capability/policy view; no guessed intent; deterministic admission retained. | Richer intent constraints (D6), enforcement of new action families when actually supported. |
| FR-12 / QR-03 | Exact core request plus actual provider exchange, call-level failures and full trace separated from decision summaries. | Schema, durable storage, provenance metadata, replay and completeness tests. |
| SR-10 | Runtime-visible evidence/history only; no hidden-reference fields, paths or credentials. | Concrete schema checks and runtime-boundary tests, beyond import restrictions. |

Task 2 review completed by the owner's instruction to proceed to Task 3 on 2026-11-04. The following sections define the proposed verification and restoration contract.

## 18. Task 3: verification inputs and responsibilities

EVALUATE in the source lifecycle means runtime verification. A pure function in `core.verification` will compute checks and a verdict; the loop supplies the inputs and routes the result. The current `validate_verification_references` checks consistency only. It cannot stand in for that computation.

Each assessment must receive explicit immutable evidence for:
- The fixed `AcceptanceCriteria`, including identity and version.
- The assessed state: the starting state for INITIAL, or the tentative candidate for ATTEMPT.
- The selected observations for that state, with their original measurement provenance.
- For ATTEMPT: the complete saved `before` state and its observations, and the fixed initial state/observations wherever an INITIAL baseline is configured.
- Declared measurement-subject definitions and analysis settings sufficient to establish comparability.

Result identity is supplied through the run's identity mechanism. No verifier file access, provider call, new measurement, threshold tuning or hidden-reference lookup is implied. The loop retains the exact assessment inputs alongside its `VerificationResult`; check evidence IDs alone are not a complete audit trail.

### Baseline selection

| Condition | Value(s) assessed |
| --- | --- |
| Target | The assessed state's metric compared with its configured absolute threshold. |
| Minimum improvement | Candidate versus this attempt's `before`, which is the last accepted state. It does not use the original mix or the previous rejected candidate. |
| Absolute guard | The assessed state's metric compared with its configured absolute bound. |
| Delta guard with ATTEMPT baseline | Candidate versus the saved `before` for this attempt. |
| Delta guard with INITIAL baseline | Candidate versus the run's fixed initial reference, even after several accepted edits. |

Capture initial-baseline evidence before edits can supersede its audio membership. Do not reconstruct it from later accepted outputs. Unsupported/missing baseline evidence must be reported, not replaced by the nearest available attempt or a zero value.

## 19. Observation compatibility

A numeric value is usable only when it measures the intended thing under compatible conditions. Matching a metric name or track name alone is insufficient.

For every condition and required state:
1. Resolve exactly the configured metric `VersionRef` and exact `AnalysisScope`, including track ordering where the metric uses it, project-time interval and frequency range. Do not average a larger scope or substitute a neighboring band.
2. Resolve its declared measurement subject: which STEM/GROUP/MIX or ordered combination of inputs the metric measures. Candidate artifact IDs may differ from baseline IDs; their declared roles and contributing tracks must correspond.
3. Validate observation provenance against the appropriate state's checkpoint and its recorded artifacts. Before-state observations are checked against `before`, not against the candidate's membership.
4. Require the declared analysis specification, analyzer/method version, relevant parameters and preprocessing to match the frozen comparison policy. Runtime provenance must identify the actual configuration used; a version label is not permission to change it silently.
5. Require compatible sample rate, channel layout, project-time origin and coverage, and the declared alignment assumptions. Existing metadata checks do not prove content alignment. An undeclared offset, resampling or independently normalized comparison is not allowed.
6. Require exact unit agreement between values and their condition threshold/delta. No implicit dB/dBFS conversion, percentage interpretation, spelling normalization or scale conversion.
7. Use a finite `MeasuredScalar`, or record explicit unavailability. Never interpret `Unavailable` as zero.

The current `MetricSelector` and duplicate-response key contain only metric and scope. They do not identify an artifact role independently. Before implementation, either the versioned metric definition must unambiguously declare its input subject(s), or the selector/observation contract must be extended explicitly. For example, measuring two stems is not interchangeable with measuring their summed mix merely because the contributing tracks are identical. Do not work around an ambiguous selector by choosing the first observation.

Input-export and candidate-render provenance need not have identical reference labels: an original exported WAV and a new candidate can have different render references while retaining comparable sample geometry and declared processing semantics. Equally, artifact production revisions and hashes are expected to differ after a change. Compatibility must follow an explicit policy over the settings and roles, not require equal provenance IDs or assume that unequal IDs are harmless.

### Missing evidence versus invalid input

- A structurally invalid response (wrong source/project, conflicting duplicate identity, invalid record or violated response contract) is recorded as a response/wiring failure at its boundary. Do not fabricate a measurement-based verdict from untrusted records.
- A configured condition with no matching compatible measurement, an explicit `Unavailable` result, a unit/configuration mismatch that prevents comparison, or an unavailable baseline receives `CheckStatus.UNAVAILABLE` with a specific explanation. It cannot PASS or FAIL numerically.
- Criteria configuration errors detectable before execution, such as a threshold whose unit disagrees with its declared metric definition, must fail run setup rather than consume a correction attempt.
- Two observations with the same ID but different contents are invalid. Reusing an identical earlier observation is possible only when all its actual source artifacts, measurement dependencies and settings remain valid; record the reuse. Age alone is not an incompatibility, and unchanged artifacts are never retagged.
- Metadata references do not prove the analyzer used the claimed bytes. The analyzer/integrity boundary must establish captured-input integrity for actual analysis. Its evidence must be linked to the observations; this remains an integration contract, not a property obtained from constructing `Observation`.

## 20. Deterministic check computation

All condition IDs are unique. Produce exactly one check per configured condition, in configuration order: targets, improvements, guards. Preserve input observation references and explanations. If the same observation is legitimately reused for both operands, its evidence ID need not be duplicated in one check.

Let `x` be the assessed value, `b` the selected baseline, `t` an absolute threshold, `m` a strictly positive minimum improvement, and `d` a nonnegative maximum absolute delta. These are scalars in the configured identical unit; they are not musical defaults.

| Check | PASS rule |
| --- | --- |
| Target / absolute guard with LT | `x < t` |
| Target / absolute guard with LE | `x <= t` |
| Target / absolute guard with GT | `x > t` |
| Target / absolute guard with GE | `x >= t` |
| Improvement with LOWER direction | `b - x >= m` |
| Improvement with HIGHER direction | `x - b >= m` |
| Delta guard | `abs(x - b) <= d` |

An absolute delta guard constrains movement in either direction; it is not silently reinterpreted as a directional worsening allowance. Failure of a minimum-improvement check can mean no change or insufficient progress, not necessarily a regression. Only declared checks establish the reported distinction.

Use the declared numeric semantics without an invented epsilon, rounding, clipping or normalization. If a derived difference is not finitely representable even though its operands are finite, mark that computation UNAVAILABLE with its reason. Do not turn infinity into a successful improvement or conceal a programming error with a catch-all.

### INITIAL mode

- Evaluate targets and absolute guards against the initial observations.
- Mark improvement checks and all delta guards NOT_APPLICABLE, as required by the existing contract. Do not compare a state with itself and claim an improvement.
- A known failure of a target or absolute guard yields REJECTED; this means the initial result is not sufficient, not that a rollback occurred.
- If none of those applicable checks FAIL but one is UNAVAILABLE, the verdict is INCONCLUSIVE.
- If all applicable checks PASS, the verdict is ACCEPTED with SATISFIED targets. The loop can finish without a decision/render call.
- Before a corrective attempt, required comparison baselines and decision evidence must nevertheless be available. An INITIAL rejection is not blanket permission to act while required evidence is missing.

### ATTEMPT mode

Every configured check is applicable; NOT_APPLICABLE is invalid. All configured checks must be available for acceptance. This preserves the existing `VerificationResult` rule: even if targets are satisfied, an unavailable configured improvement check prevents acceptance. A failing but available improvement check is different: it does not block acceptance when targets are satisfied.

Targets and guards remain fixed during the run. The LLM cannot drop a failing check, change its threshold or select a different baseline after seeing candidate measurements.

## 21. Verdict and state-selection precedence

Compute `target_status` independently of the overall verdict:
- At least one target FAIL: UNMET, even if a different target is unavailable.
- No target FAIL, but a target is UNAVAILABLE: UNKNOWN.
- Every target PASS: SATISFIED.

For ATTEMPT assessments apply these rules in order:

| Priority | Evidence | Verdict | State disposition |
| --- | --- | --- | --- |
| 1 | Any guard FAIL | REJECTED | Never promote; reconcile/restore the saved accepted reference. Other unavailable checks do not cancel this known rejection. |
| 2 | Targets UNMET and any improvement check FAIL | REJECTED | A known unmet target and failed required improvement are sufficient to reject, even if another check is unavailable. |
| 3 | Any configured check UNAVAILABLE, with no decisive rejection above | INCONCLUSIVE | Never promote. Keep/re-establish the last accepted state; remeasurement requires a later bounded policy. |
| 4 | Targets SATISFIED, every guard PASS, all checks available | ACCEPTED | Complete success, even if an improvement check FAILS. |
| 5 | Targets UNMET, every improvement and guard PASS, all checks available | ACCEPTED | Accept intermediate progress; only iterate if the run policy and remaining budgets permit. |

All targets, all configured improvement requirements for an unmet target, and all guards are conjunctive. Do not average them into a score or pick whichever target improved most. An UNKNOWN target with a failing improvement is inconclusive unless a separate decisive rejection is established: the missing target evidence could otherwise determine whether improvement was required for acceptance.

An empty guard collection is structurally permitted today. It establishes no side-effect protection. Coverage and any methodological requirement for nonempty guards must be explicit in run design; the loop must not describe an unguarded result as having demonstrated that other metrics were preserved. This design supplies no scientific guard metrics or thresholds.

The pure verifier returns its result; it does not choose the accepted reference, allocate resources or emit a VERIFIED rollback receipt. The loop validates the result and applies the state transition at the acceptance/recording boundary defined in Task 4.

## 22. Restoration: exact state and actual evidence

Rendering produces new files while `before` remains saved. Rolling back a rejected candidate normally means retaining/selecting the old accepted artifacts and settings; it does not require applying the inverse gain or rerendering an old mix. Rerendering may change WAV bytes and cannot stand in for exact restoration of the recorded file hash.

The proposed integrity/recovery boundary must establish:
1. The intended saved project, revision and checkpoint, and its relationship to the execution being reconciled.
2. Exact saved snapshot, processing settings, fixed input references and audio membership. Keep the original snapshot timestamp; record the later verification time separately in evidence.
3. Availability and integrity of every saved current audio member and fixed input needed for the complete restored state, against the saved metadata/hashes through explicitly configured roots.
4. Selection/publication of the intended accepted reference wherever such an external reference exists. If no external publication exists in the initial controlled implementation, state that limit explicitly.
5. A linked report of the operations and observed checks, including failures, retained candidate/partial files and any inspection uncertainty.

Physical checks belong to adapters; state/receipt consistency checks belong to the core. `validate_rollback_references` verifies equality of recorded metadata, not those physical claims. Do not issue VERIFIED solely because `accepted` still refers to the same Python object.

| Restoration outcome | Required interpretation |
| --- | --- |
| VERIFIED | Exact saved state is selected and the required integrity/reference checks succeeded. A matching `RollbackReceipt` can support ROLLED_BACK. Rejected output files may still exist and remain unaccepted. |
| FAILED | Established evidence shows the restoration obligation could not be fulfilled, for example a missing saved artifact or a hash mismatch without an established restoration. Do not claim the old output is restored. |
| UNCERTAIN | The required checks or accepted-reference selection cannot be established. Preserve the uncertainty and observed effects; do not guess. |

A failed/uncertain restoration prevents further edits until the accepted state is re-established. Final run outcome/reason and budget accounting are Task 4. Recovery cannot be silently retried without allowance or reported as verified merely because no recovery allowance remains.

Keep execution uncertainty separate from accepted-state integrity. A failed candidate or partial file does not prove the saved accepted state is damaged; conversely, a known failed render does not prove the saved files are intact. Trace both facts independently.

Rejected and partial files are retained in their assigned locations with their disposition recorded. No deletion, rename, ownership transfer, race-free confinement, crash durability or atomic two-file publication is introduced by this contract. Existing filesystem checks have their documented limits; exact inspection describes captured evidence, not a guarantee against later concurrent modification.

## 23. Conformance cases for the implementation plan

These are scalar/record scenarios for future unit tests, not audio-performance evidence. Illustrative values use a test-only metric/unit and do not define a mixing threshold.

| Case | Expected result |
| --- | --- |
| INITIAL targets and absolute guards PASS | ACCEPTED / SATISFIED; improvements and delta guards NOT_APPLICABLE; no correction needed. |
| INITIAL applicable evidence unavailable with no known failure | INCONCLUSIVE; not successful and no guessed baseline. |
| Target is `x <= 5`, minimum LOWER improvement is `1`; before `9`, candidate `7`, guards PASS | ACCEPTED / UNMET intermediate progress. |
| Same criteria, before `5.2`, candidate `5`, guards PASS | ACCEPTED / SATISFIED despite improvement of only `0.2`. |
| Candidate meets target but a guard FAILS | REJECTED; no promotion. |
| Targets UNMET; one required improvement FAILS | REJECTED if the failure is established, including when unrelated checks are unavailable. |
| Targets UNKNOWN; improvement FAILS; guards do not FAIL | INCONCLUSIVE; target satisfaction has not been established. |
| Targets SATISFIED but a configured improvement is UNAVAILABLE | INCONCLUSIVE under the current all-configured-checks-available rule. |
| Target equality at a strict versus inclusive boundary | LT/GT fail on equality; LE/GE pass. Delta equality and minimum-improvement equality pass. |
| Two accepted edits followed by an INITIAL delta guard | Compare with the original initial observations, not the preceding accepted candidate. |
| Metric/version/scope/unit or subject mismatch | No numeric PASS; configuration errors fail setup, unavailable compatible evidence yields UNAVAILABLE. |
| Same tracks but stem-pair metric supplied with a summed-mix subject | Reject/substantiate incompatibility; do not substitute the value. |
| Before/candidate IDs and production revisions differ normally | Compare corresponding declared subjects; do not demand equal production IDs. |
| Observation from unchanged retained sources reused | Allowed only with identical source/dependency/settings validity and recorded reuse. |
| Finite operands produce a non-finite delta | UNAVAILABLE computation; no fabricated improvement or clipping. |
| Re-analysis fails after candidate creation | No accepted result; preserve the candidate and failure evidence, reconcile the saved state. |
| Rollback metadata matches but a saved file hash does not | Never VERIFIED or ROLLED_BACK. |
| Exact saved files intact, rejected candidate files remain | Verified restoration is possible; leftover files do not become accepted checkpoint members. |
| Recovery cannot establish accepted-reference selection | UNCERTAIN; no further edit and no successful rollback claim. |

## 24. Task 3 scope and remaining decisions

This contract defines deterministic comparison and routing semantics without selecting a scientific metric, calibration threshold or perceptual success criterion. It contributes to FR-09/FR-10 and traceability requirements, but fulfills none by documentation alone.

Before implementation, the combined design must specify the immutable assessment-input schema and measurement-subject declaration, how metric/settings/unit definitions are supplied, actual input-integrity evidence, and the adapter-facing restoration/resource interface. Those needs must be resolved explicitly in the final contract/implementation planning, not delegated to an implementer's guesses or satisfied by fabricated receipts.

Design consistency check on 2026-11-04: a read-only scalar/record probe instantiated the proposed table for two targets, two improvements, an absolute guard and a delta guard. All 729 ATTEMPT PASS/FAIL/UNAVAILABLE combinations and all 27 INITIAL applicable-check combinations produced records accepted by the existing constructor/reference invariants. This checks compatibility of the proposed outcome table with existing records; it is not an implemented verifier, a numerical-method test or research evidence.

Task 3 review completed by the owner's instruction to proceed on 2026-11-04. The following sections complete the proposed run-level design.

## 25. Task 4: one serial run and its limits

One deterministic loop owns a run. It performs one decision or effect at a time; there are no concurrent edits, background retries or overlapping attempts within that run. Drivers compose adapters and start the loop. Re-entering a completed run or starting an automatic context wait is not supported in the first version.

Keep the existing `BudgetLimits` / `BudgetUsage` dimensions. Define their semantics before changing their shape:

| Counter | When a unit is charged | What is not charged |
| --- | --- | --- |
| `decision_calls` | On acknowledgement of each decision-dispatch intent, before `DecisionMaker.decide`, including a provider retry. | Building inputs; a failed pre-intent budget check. |
| `attempts` | When a structurally valid ProposeAction response enters action checking, before its semantic evidence gate/admission/preflight. | No Change, abstention, malformed responses and provider transport retries. |
| `render_calls` | On acknowledgement of a render-dispatch intent, before the run-level rendering operation, which includes resource preparation and actual rendering. | Pure admission/preflight and a failed pre-intent budget check. |
| `recovery_operations` | Before one complete reconciliation workflow: resolve any uncertain accepted-reference commit and check the selected saved state's integrity. A workflow contains at most one authority read and one integrity check, with no hidden retries. | Holding/releasing a recovery reservation; successful rendering that needs no recovery. |
| `elapsed_seconds` | Monotonic time since loop entry at each recorded checkpoint, including waiting, provider calls, analysis, rendering, recovery and recording already observed. The returned completion receipt supplies the post-finalization checkpoint. | Do not silently exclude an observed stage duration to make the number fit the configured limit. |

These are conservative charged dispatch slots. A charged dispatch can be blocked or interrupted before invocation/completion; history separately records whether invocation actually began and what completion/effects were observed. Never refund an acknowledged slot, and do not equate an intent event with proof that a remote request completed. The exact pre-dispatch durability ordering is section 29.

Before any charged operation require `usage + 1 <= limit`. At equality the already permitted operation may complete; the next one is blocked. Zero limits are valid. Initial observation and an already-satisfactory result do not require decision/attempt/render allowance. Limit checks are specific to the next needed operation, not a blanket rejection of configurations containing zero.

The exact `Situation.usage` snapshot includes the charge for the current decision dispatch. Prior history includes completed earlier calls, not the not-yet-completed current call. Capture that snapshot and call identity before calling the port; do not mutate it afterwards.

### Bounded analysis and iteration

For the initial loop, perform one initial analysis and at most one candidate analysis per successful render. Reuse compatible accepted observations when OBSERVE assembles the next decision input; assembling feedback does not force another physical analysis of unchanged inputs. There is no automatic analysis/remeasurement retry in the first version.

Thus analysis calls are bounded by `1 + render_calls`. All subsequent decision rounds consume `decision_calls`; valid action proposals also consume `attempts`. An invalid, rejected or repeated unhelpful proposal cannot create an unbounded loop. A later analysis-retry feature must introduce an explicit allowance and update this bound.

## 26. Retry policy and deadline behavior

Add a required immutable retry policy for the decision port: a nonnegative maximum retry count per logical decision round, with no undocumented default. The original call is not a retry. No provider-internal automatic retries are allowed in the initial adapter: each actual remote invocation must pass through the charged decision boundary. A future adapter using multiple remote calls per invocation must expose/account for them explicitly before it is admissible.

Initial retry rules:
- Retry only an expected `PortFailure` with `code == UNAVAILABLE` and `retryable == True`.
- INVALID_INPUT, UNSUPPORTED, INVALID_RESPONSE and INTERNAL_ERROR are terminal for this round. Do not treat programming exceptions or invalid structured responses as transient provider transport failures.
- A retry keeps the same accepted state, context, diagnosis and objectives. It gets a new call identity and an updated usage/history snapshot that records the preceding failed call. The actual input is captured again; do not claim the serialized retry request is byte-identical.
- The retry count belongs to the logical round and does not reset after another failure. Check global time/call allowance before checking/using another local retry slot.
- No automatic render retry, recovery retry or schema-repair prompt in the first version. A new render after a verified rejection is a new proposal/attempt with fresh identities and workspace, not a retry into a partly used directory.
- Retry without an internal sleep in the initial stand-in loop. A real provider adapter must honor any required service delay within the declared time allowance, with the delay recorded. Delay/backoff behavior must be specified before connecting the real provider; it must not be invented inside the SDK.

OP-04 is not complete until nonzero bounded retries and eventual exhaustion are tested with a stand-in and the provider adapter honors the same accounting. Configuring zero retries is valid but does not demonstrate retry behavior.

### Deadline

Use an injected monotonic clock for accounting and deterministic tests; UTC timestamps describe chronology, not elapsed-time comparisons. A backward/invalid monotonic reading is a runtime-contract failure, never negative usage or extra allowance.

Before dispatching ordinary effects, require elapsed time strictly below `max_elapsed_seconds`; equality blocks dispatch. On every return, update actual elapsed time before deciding the next operation. Adapters must receive/use the remaining allowance where they support deadlines. If a call finishes late, record the overrun and start no further ordinary work. A later response cannot silently reset the deadline.

Recheck time and cancellation after dispatch-intent acknowledgement, immediately before the effect. Acknowledgement itself may have exhausted time. The already authorized count slot is not checked/charged a second time. If dispatch is then blocked, record NOT_DISPATCHED when recording remains possible, retain the conservative charge and start no effect. Apply the time/cancellation gate before acceptance-commit dispatch too: a verified candidate cannot begin acceptance after expiry/cancellation.

This first in-process loop provides cooperative dispatch-time enforcement, not guaranteed interruption of arbitrary blocking native I/O or DSP. A hard wall-clock bound requires enforceable adapter timeouts or process isolation and remains a separate integration requirement. Existing usage records intentionally permit overruns so actual behavior remains visible.

### Recovery reservation and exhausted limits

Before dispatching any render, require at least one unused recovery-operation slot and reserve it for this attempt. A reservation is not a charge. Admission/preflight and render-allowance checks must pass before creating physical resources.

- An accepted, committed candidate releases the unused reservation.
- A non-accepted/failed effect consumes the reserved slot when the reconciliation workflow is dispatched. This includes a failed resource-preparation call that may have created a directory or other resources.
- Recovery does not need a decision, attempt or render allowance, so exhausting those counters does not prevent use of the reserved recovery slot.
- Recovery still needs time below the same deadline. There is no implicit unlimited emergency allowance. If recovery cannot be dispatched or cannot finish establishing the saved state, report the unresolved final-state obligation explicitly and do not claim successful rollback.
- No more than one recovery workflow is dispatched for an attempt in the initial version. Its failure/uncertainty terminates the run; it never spends a second slot through an internal retry.

Gate each external suboperation of recovery separately. A timely workflow start does not authorize an integrity call after an authority read returns late. Record whatever authority fact was actually established; if the remaining required check is blocked, report unresolved integrity/recovery rather than VERIFIED.

Finalization/recording of already-observed facts must be attempted even after the work deadline, under the store adapter's bounded operation policy. It cannot start an edit or erase an overrun. Additional observed finalization time is included in the returned completion checkpoint, as distinct from the earlier terminal-event snapshot (section 29); this is another reason not to claim a strict hard deadline yet.

## 27. Iteration and terminal outcomes

Continue only from a known accepted state and within the next operation's allowances:
- ACCEPTED / SATISFIED: commit the accepted state, then finish successfully if the commit/finalization and deadline conditions support success.
- ACCEPTED / UNMET: commit intermediate progress, then assemble the next Situation from that accepted state and feedback.
- REJECTED candidate: establish verified restoration, then a new decision round may use the rejection feedback. Repeated ineffective actions remain bounded by the decision/attempt/render limits.
- INCONCLUSIVE assessment, analysis failure or known render failure: reconcile the saved state and terminate in the initial version. Do not automatically repeat rendering or analysis to manufacture available evidence.
- Non-action decision: finish with its recorded reason. Missing context follows the approved end-run/new-run policy.

Retain the five coarse `RunOutcome` members. Introduce stable enumerated terminal reasons and a separate human-readable explanation; do not keep the existing free-text field as the only machine-readable stop reason. Exact field/type signatures will be pinned in the implementation plan.

| Coarse outcome | Proposed reason codes / triggers |
| --- | --- |
| SUCCEEDED | INITIAL_CRITERIA_MET; CANDIDATE_CRITERIA_MET. Requires sufficient verification and a known accepted reference. |
| ABSTAINED | NO_CHANGE_PROPOSED; INSUFFICIENT_EVIDENCE; MISSING_CONTEXT; CONFLICTING_CONSTRAINTS; NO_JUSTIFIED_ACTION. Preserve the actual decision variant separately: this coarse grouping does not rewrite No Change as a provider Abstain. |
| LIMIT_REACHED | DECISION_CALL_LIMIT; ATTEMPT_LIMIT; RENDER_CALL_LIMIT; RECOVERY_CAPACITY_LIMIT; ELAPSED_TIME_LIMIT. |
| FAILED | INITIAL_ANALYSIS_FAILED; INITIAL_ASSESSMENT_INCONCLUSIVE; REQUIRED_BASELINE_UNAVAILABLE; DECISION_FAILED; INVALID_DECISION_RESPONSE; DECISION_RETRIES_EXHAUSTED; EXECUTION_PREPARATION_FAILED; RENDER_FAILED; INVALID_RENDER_RESPONSE; CANDIDATE_ANALYSIS_FAILED; VERIFICATION_INCONCLUSIVE; ACCEPTED_STATE_INTEGRITY_FAILED; ACCEPTED_STATE_INTEGRITY_UNCERTAIN; RESTORATION_FAILED; RESTORATION_UNCERTAIN; REQUIRED_RECOVERY_NOT_PERFORMED; RECORDING_FAILED; ACCEPTANCE_COMMIT_UNCERTAIN; RUNTIME_CONTRACT_FAILED. |
| CANCELLED | USER_CANCELLED. Cancellation is checked at safe boundaries; it cannot interrupt an effect and assert that nothing happened. |

Every proposed reason must have a reachable test. If implementation combines or splits codes, update the reviewed reason table and exhaustive tests together; no arbitrary fallback string. An unsupported/insufficient diagnosis that prevents constructing a justified request is recorded as an insufficient-evidence abstention with provenance, not invented measurements.

### Primary reason precedence

Keep all contributing failures/events in the trace, but choose one primary reason deterministically:
1. Unresolved recording/acceptance authority or failed/uncertain required restoration takes precedence over the original rejection, cancellation or limit. An uncertain final state cannot be concealed by an ordinary success/limit reason.
2. A non-retryable stage failure takes precedence over a secondary limit detected while handling that failure. When an otherwise retryable decision failure is returned, check time limit, then global decision-call limit, then local retry exhaustion.
3. At a clean boundary, a requested cancellation precedes starting further work. Otherwise elapsed-time exhaustion precedes the specific next operation's count limit. Do not check irrelevant operation limits.
4. Sufficient verification becomes SUCCEEDED only after the necessary acceptance/finalization boundary. If the operation/commit that established it returned after the deadline, preserve any acceptance that actually committed but report the elapsed limit rather than pretending it completed within time.

An initial known unmet target is not a terminal failure by itself. It starts correction planning when required decision/baseline evidence and resources are available.

### Final project-state disposition

Report this independently of run outcome:
- UNCHANGED: the original selected state remained the accepted state without a completed restoration transition or accepted edit.
- ACCEPTED_EDITS_RETAINED: the selected known accepted state contains committed accepted edits and no later successful restoration is the final transition.
- RESTORED_TO_LAST_ACCEPTED: the last relevant transition is a verified restoration to `before`, even if `before` already contains earlier accepted edits. Persist the actual selected state/revision so the label cannot erase that history.
- UNKNOWN: the required final accepted selection or integrity cannot be established. Never pair UNKNOWN with SUCCEEDED.

A non-action decision after accepted intermediate edits retains those edits. A failed candidate or leftover file does not automatically mean UNKNOWN; conversely, retaining an old object does not prove a known final state.

## 28. Proposed records and effect boundaries

This section resolves the integration alternatives left open in sections 9 and 24 at the semantic level. Names below are proposed public vocabulary; the implementation plan must give exact frozen-record signatures, imports and tests before coding.

### Run-level rendering request

Choose a run-level request to `Renderer.process` rather than returning adapter handles to the loop. A proposed `RenderRequest` contains the preflight `RenderPlan`, complete `before_state` and explicit logical source-location/protection bindings. A binding associates the exact `AudioArtifact` record with a configured root key; physical root resolution stays in the adapter.

This closes a real gap: after the first edit, original stems and newly accepted outputs may resolve against different configured roots. Do not rewrite `AudioArtifact.location` to an absolute path or infer a root from a relative filename. New outputs inherit the root key assigned by the plan's logical workspace; record their bindings with the result.

The driver composes a run-level rendering adapter that resolves protection inventory, allocates fresh per-attempt resources and calls the existing workspace-bound `GainMixProcessor`. The inner processor need not become a lifecycle owner. A real wrapper is a later integration deliverable; the first loop uses a faithful stand-in.

Known artifact bindings cover initial/current members, fixed inputs and retained known artifacts. Partial resources whose full metadata/hash is unknown remain structured resource observations, not fabricated `AudioArtifact` records. Never reuse an earlier execution's workspace/token; preserve observed retained-resource paths and unknown effects. Structured failure-resource evidence must accompany the outer render outcome; issue-message parsing is insufficient.

Changing the rendering-port request deliberately requires rebasing its public-contract tests. Existing inner processor tests remain useful; do not silently claim that its old `process(plan)` signature already satisfies the revised outer port.

### Integrity/recovery and recording

- A state-integrity port inspects an explicitly selected complete state and its logical bindings, returning linked per-artifact evidence or expected failure/uncertainty. It does not choose the accepted state or call a recording adapter's internals.
- A journal port owns ordered event recording and, in the durable integration, the authoritative accepted-state commit. Its read/query resolves uncertain acceptance acknowledgements.
- The loop composes authority and integrity evidence into distinct reconciliation results: confirmed candidate acceptance, verified restoration to `before`, or unresolved authority/integrity. Only the restoration branch constructs a RollbackReceipt. Confirming an already committed candidate is not rollback; its final known state is ACCEPTED_EDITS_RETAINED. Neither a rendering adapter nor a recording adapter imports the other's internals.
- Clock and identity sources are explicit injected dependencies. They are runtime collaborators, not callable fields stored inside core records.

The initial integrity check is a bounded initialization operation, not a recovery from a correction. Candidate failure/rejection reconciliation is charged as recovery. Its workflow must use the same configured roots/protection and ordinary runtime artifacts, never hidden research references.

### Acyclic ownership map

| Owner | Responsibility |
| --- | --- |
| `core.records` lower-level feedback/capability/measurement definitions | Immutable summaries and subject/action descriptors that `Situation` can consume without importing run/admission back into decision records. |
| `core.records.decision` | Situation, decision variants and response; references only lower-level data. |
| `core.records.rendering` | Render plan/request, logical input/protection bindings and structured render outcome/evidence. |
| `core.records.run` and a downstream trace owner module | Run limits/configuration/terminal record, complete ordered call/attempt events and returned report. The full trace may contain Situation; Situation contains compact summaries, not that trace. |
| `core.ports` | Analysis, decision, rendering, integrity and recording contracts. No sample arrays or adapter instances cross these boundaries. |
| `core.verification` | Pure check computation and verdict precedence. |
| `core.loop` | Stage order, resource accounting, retries, trace assembly, accepted-state selection and recovery composition. |
| `adapters` / `drivers` | Actual analysis/provider/render/file/store operations and composition. |

The complete record dependency graph must be checked when exact types are chosen. Do not bypass a cycle through local imports, mutable callbacks, Any/object payloads or package re-exports.

## 29. Recording and acceptance boundary

The initial milestone is an explicitly NON_DURABLE loop with an in-memory journal and controlled ports. It demonstrates stage order, decisions and state selection under injected failures, not crash recovery, production file protection or FR-12 completion. Its returned report must state that durability mode explicitly.

The same semantic boundary is defined now so real integration cannot silently use an unsafe ordering:
1. Assemble an immutable dispatch-intent event with run/call/attempt identities, sequence, exact input, selected accepted reference and the next charged usage. Record it and require acknowledgement before the corresponding effect is dispatched.
2. Apply the charged usage, recheck the time/cancellation gate and dispatch at most once. If recording fails first, invoke no corresponding provider/render effect. A gate failure after acknowledgement retains the charge but records that the effect was not dispatched. On interruption between an acknowledged intent and observed completion, treat the allowance as consumed and the outcome as pending/uncertain; do not blindly repeat it.
3. Capture the actual returned/raised outcome. Record expected failures, validation errors, observed partial effects and uncertainty. Unexpected programming exceptions remain errors; a narrow outer finalization guard may preserve observed facts before re-raising, but must not fabricate a normal PortFailure.
4. For a verified candidate, prepare one idempotently identified acceptance event containing the complete candidate reference, verification result, execution/evidence links and expected prior accepted reference/sequence.
5. In durable mode, the journal's atomic acceptance-commit contract makes this event and the selected accepted state one authoritative fact. The accepted state is derived from the committed journal prefix; a separate manifest/pointer is a materialized view, not a competing authority. A real store must implement/test this guarantee before claiming durable acceptance.
6. Only after an acknowledgement of that commit does the live loop promote its in-memory selected state and continue/finish. Acknowledged in-memory journal commits are only logical commits in NON_DURABLE mode.

On a definitely rejected/not-written commit, retain the old accepted reference and reconcile it before terminating for recording failure. A failure to acknowledge is not proof of non-commit: query by the acceptance event's idempotency identity under the reserved recovery workflow. If the commit exists, check/retain that committed candidate; this is confirmed acceptance, not a RollbackReceipt. When authority and integrity are established within the limits and the reconciliation result is successfully recorded, the loop may follow the original accepted verdict (finish on satisfied targets or continue from intermediate progress). If recording that result fails, terminate for recording failure rather than starting more ordinary work. If absence is established, reconcile `before`; if authority cannot be established, report acceptance uncertainty. If the committed candidate's integrity fails/is unknown, use the corresponding ACCEPTED_STATE_INTEGRITY reason and do not pretend the previously saved reference remains authoritative. Do not blindly roll back to a state that may no longer be the authoritative accepted reference.

Do not replay an ambiguous acceptance write as a new acceptance. Queries and retry of an idempotent storage operation must have bounded store behavior. The initial reconciliation workflow allows one authority read; inability to establish the answer terminates with uncertainty.

### Terminal snapshot and completion observation

The terminal event records the selected state, charged usage and elapsed checkpoint, proposed outcome/reason, explanation and unresolved obligations immediately before its own recording call. It is a terminal intent/snapshot, not evidence of its future acknowledgement or latency.

After that single bounded finalization attempt returns or fails, the returned report carries a separate completion receipt: acknowledgement/failure status, the observed end-to-end elapsed checkpoint and the effective final outcome/reason. A recording failure supersedes a proposed success with FAILED / RECORDING_FAILED; a late finalization acknowledgement supersedes a proposed SUCCEEDED or ABSTAINED result with LIMIT_REACHED / ELAPSED_TIME_LIMIT, subject to higher-priority unresolved-state/failure precedence. It does not replace an already established FAILED, CANCELLED or LIMIT_REACHED reason merely because finalization is also late; record the overrun separately. Keep the earlier event immutable and explicitly labelled as proposed; do not expose it as the effective completed-run result when its completion receipt disagrees.

Do not try to persist a record containing the duration of its own future write. Durable integration must publish the resulting completion envelope separately, referencing the journal prefix; only an acknowledged completion publication supports a complete durable report. Publication has a separately observed bounded-store receipt/latency rather than recursively rewriting the outcome to include its own future latency. Until that integration exists the report is NON_DURABLE. A failed publication is a reporting failure with no complete durable run, not retroactive removal of an acceptance that actually committed. This distinction must be present in read-back/replay design.

If terminal recording fails, preserve the known return/in-memory facts and surface the recording failure. No external side effect can be undone by rewriting the result object. An unexpected original programming exception remains the raised exception if recording also fails; surface the recording failure in the available trace/diagnostic channel without replacing the original error.

The existing exclusive WAV writer does not supply journal atomicity, fsync, transactions across outputs or durable publication. Those are separate storage/integration implementation obligations. No new claim of these guarantees follows from this design.

## 30. Complete run report and provenance

Return a structured immutable report containing the effective terminal RunRecord, initial reference, selected final state when known, durability mode, completion receipt and ordered events (including the earlier proposed terminal snapshot). Exact record shapes are the next implementation-planning step. Store the following information in the complete trace:
- Schema version, run/project identities and ordinary variant identifiers that encode no injection details.
- Full runtime configuration: criteria and metric/subject definitions, actual analysis/render settings, processing specification, context/provenance, policy/capability view, retry/resource limits and configured runtime roots/bindings.
- Code/build identity and dirty-state provenance where applicable; dependency/native-library versions needed to interpret numerical behavior.
- Initial/current/candidate artifacts and hashes, fixed inputs, source bindings, retained-resource observations, accepted-reference commits and restoration proofs.
- Every call's exact Situation, actual provider/model identifier and request parameters, prompt/template version, serialized request and raw response or expected failure. Exclude credentials and authorization headers; model text is untrusted data.
- Diagnosis candidates/coverage, observations, proposal/non-action variants, admission/preflight outcomes, render evidence, all verification checks and exact reasons for state transitions.
- Call/attempt/event ordering, timestamps, charged usage, overruns and all terminal reasons/explanations. An invalid response still has its call identity even if no valid Decision was constructed.

Complete trace and compact decision feedback are different immutable views. Derived summaries must be traceable to original events; a lossy provider-history reduction is recorded separately and cannot replace the source events.

Replay must not call the provider. It reads the selected accepted references and/or replays the stored actions against the captured configuration and fixed inputs with explicit compatibility checks. Selecting retained artifact bytes can reproduce their hashes; rerendering equivalent samples may produce different WAV bytes because of container metadata. Do not claim byte-identical rerendering from a numerical determinism test.

Durable schema serialization/read-back, source/environment compatibility, replay tests, retention and event-store recovery belong to the later storage/integration increment. A memory-only report is partial groundwork for FR-12/QR-03/QR-08, not completion. Every run and execution uses fresh assigned identities; destination collisions fail rather than overwrite prior runs (OP-02).

## 31. Proposed run-level conformance scenarios

| Scenario | Required observation |
| --- | --- |
| Already-satisfactory initial state with zero edit/call limits | Successful initial verification may finish without decision/render; initial evidence/preconditions still apply. |
| Exactly one decision allowance | One dispatch is charged; a retry or next decision is blocked. |
| Rejected valid proposal | Attempt and decision charges remain; no rendering; rejection appears in next feedback. |
| Zero recovery capacity | A new edit is blocked before resource preparation; never perform an edit that relies on nonexistent recovery capacity. |
| Transient decision failure then recovery | Each provider invocation is charged and captured; local retries do not reset; same accepted state/context remains. |
| Repeated transient failure | Global time/call limit or local retry cap terminates with the declared precedence. |
| Invalid provider response or programming exception | No fabricated provider decision and no automatic schema-repair retry. Programming errors are surfaced after bounded finalization attempts. |
| Initial/candidate analysis fails | No hidden analysis retries; after a candidate, reconcile the saved accepted state before termination. |
| Accepted intermediate progress | Commit the candidate, release recovery reservation and reuse its compatible observations in the next OBSERVE input. |
| Rejected candidate with verified restoration | Charge one recovery workflow; feedback records rejection; a new bounded decision may follow with fresh identities. |
| Partial-output render/preparation failure | Preserve structured observed resources and uncertainty; no render retry into the same workspace; terminate after reconciliation. |
| Render/decision limit exhausted while a candidate needs recovery | Reserved recovery can run if time remains; no new decision/render is permitted. |
| Deadline reached before required recovery | No unbudgeted recovery; primary reason records the unresolved recovery obligation and final state is not falsely marked verified. |
| A dispatched operation finishes late | Actual overrun is recorded; no further ordinary work; no claimed hard interruption. |
| Acceptance commit definitely fails | Candidate not promoted; reconcile the prior accepted reference; record failure. |
| Acceptance commit acknowledgement is lost | One bounded query chooses committed candidate versus established prior state; unknown authority produces an uncertain failure, not blind rollback. |
| A context request follows accepted edits | Finish with MISSING_CONTEXT and retain the actual accepted edits; a reply starts a new RunId/context version. |
| Recording fails before dispatch intent acknowledgement | Corresponding provider/render effect is never invoked. |
| Time/cancellation blocks dispatch after intent acknowledgement | Slot remains charged, effect is recorded as NOT_DISPATCHED, and no corresponding port effect begins. |
| Recovery authority read returns after deadline | No subsequent integrity call; preserve known authority and report the remaining unresolved obligation. |
| Verification finishes after deadline, before acceptance dispatch | No new acceptance commit; reconcile only if separately permitted; no false success. |
| Authority query confirms candidate acceptance | Check that candidate's integrity and use confirmed acceptance, not a rollback receipt to `before`. |
| Terminal record cannot be stored | No claim of a complete durable run; preserve known result and surface failure. |
| Finalization acknowledgement is late or fails | Returned completion/effective result supersedes the proposed terminal snapshot truthfully; no recursive rewriting to measure a write's own future duration. |
| Monotonic clock is invalid/backward | Runtime-contract failure; no negative usage or extra allowance. |
| Each proposed terminal reason | Reachable dedicated scenario; reason-to-coarse-outcome and final-state rules are tested exhaustively. |
| Two executions/runs collide at a destination | Fail without overwrite; no automatic token/path mutation that destroys replay identity. |

## 32. Combined-design status and next deliverable

The source lifecycle and Tasks 1–3 are approved. Sections 25–31 were approved with Task 4 on 2026-11-04; they identify required changes to existing port/configuration/result signatures. They are not implemented behavior.

The first runnable milestone remains a deterministic loop with controlled ports and a NON_DURABLE in-memory journal. Real generic DSP analysis/diagnosis, provider integration, run-level filesystem preparation/recovery and durable recording/replay are separate subsequent increments. No full scientific or persistence requirement is marked complete by a successful stand-in loop.

The controlled milestone must nevertheless model and test the logical fault branches now: commit-before-lost-acknowledgement, established non-commit, unknown authority, integrity success/failure/uncertainty, post-intent dispatch blocking and finalization failure/overrun. Their production storage/filesystem mechanisms are deferred, not their orchestration semantics.

One fresh builder review of the Task 4 draft identified the deadline/suboperation gates, post-intent charging, confirmed-acceptance-versus-rollback distinction, terminal timing boundary and required controlled fault scope. These technical ambiguities were resolved in this document before handoff. No additional owner preference was inferred. The owner approved Task 4 on 2026-11-04.

| Requirement | First controlled milestone demonstrates | Remains incomplete afterwards |
| --- | --- | --- |
| FR-06 / FR-07 / FR-11 | Structured decision inputs, feedback, explicit outcomes and constrained routing with controlled decisions. | Production diagnosis, richer intent constraints and provider integration. |
| FR-09 / FR-10 | Pure comparison/verdict rules, state selection, restoration-evidence routing and enumerated stop scenarios. | Scientific metric/threshold justification and actual artifact restoration/integrity integration. |
| QR-04 / OP-04 | Charged operation limits, bounded decision retries and injected-clock dispatch enforcement. | Enforceable real-adapter deadlines/hard wall-clock guarantees; hidden SDK retry prevention in the real adapter. |
| FR-12 / QR-03 / QR-08 | Complete immutable memory trace and logical journal fault handling. | Durable codecs/read-back, provider/environment provenance from actual integrations, crash recovery, report publication and replay. |
| SR-10 / OP-02 | Runtime-schema boundary and fresh logical identities in tests. | Actual filesystem/root isolation, source/output retention checks and durable per-run storage protection. |

Next after Task 4 review: write an exact implementation plan for the first increment, including record/port signatures, file ownership, compatibility/test updates and one Claude prompt. Resolve measurement-subject and uncertainty data shapes explicitly there without choosing scientific detectors or calibrated probabilities. Keep the standing source-lifecycle brief in every prompt.

## 33. First implementation handoff

The owner approved the combined design and requested continuation to Task 5 on 2026-11-04. The first bounded implementation is specified in `../plans/2026-11-04-control-loop-budget-gate-plan.md`; the Claude prompt is `../plans/2026-11-04-control-loop-budget-gate-claude-prompt.md`. This first increment adds typed budget-check outcomes and a pure projected-charge gate only. Its implementation plan fixes those exact interfaces; it authorizes no full orchestrator, provider, verifier or persistence implementation. Subsequent increments receive their own exact plans after review.
