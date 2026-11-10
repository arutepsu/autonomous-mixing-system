# First controlled coordinator

Status: proposed for owner review. Builds on the approved 2026-11-04 attempt contract and implemented stage contracts.

## Purpose and source alignment

Deliver one executable serial run through OBSERVE, DIAGNOSE, PLAN/DECIDE, gate/admission, ACT, re-analysis/verification and acceptance/reconciliation. Slides 5–9 of C:/uni/Thesis Proposal – AI Mixing Agent.pdf are the conformance reference. Context and previous feedback must reach the decision maker, while runtime verification remains independent of the decision maker and hidden evaluation references.

The first integration uses controlled port implementations and labelled metadata responses. Real music is later supplied by the owner through FL Studio exports. Core identities and policies are track-role agnostic. Metadata ingestion for routing, effects and existing sidechain remains a subsequent integration. Scientific masking, buildup and phase methods and additional action families remain separate research work.

## Approach

The owner selected a complete coordinator design followed by three implementation tasks: runtime/trace contracts, coordinator composition, and end-to-end fault scenarios. Building further isolated helpers without integration would postpone the intended milestone. Connecting real audio/provider adapters immediately would mix orchestration bugs with method and external-service behavior.

Keep the existing pure stages and accepted-state journal. Add only collaborators and records required to connect them. First execution is explicitly NON_DURABLE and sequential within a run.

## Public entry point

The intended entry is run(request: RunRequest, services: RunServices) -> RunReport in core.loop.coordinator. RunServices is a runtime composition object holding collaborators, rather than a core business record with callable fields. Put its definition in drivers or use an explicit keyword collaborator signature in the implementation plan, following the existing architecture rule that core.loop defines functions and core.records defines data. Prefer explicit keyword collaborators for the initial implementation.

RunRequest contains RunId, initial ProjectState, existing RunConfiguration, producer ActionAdmissionPolicy, RendererCapabilities, ProcessingSpec, MixRecipe, initial stem bindings and mix artifact ID, condition/measurement bindings, alignment reference and logical root bindings. A versioned decision retry policy is explicit. The request contains no filesystem handles, credentials or hidden evaluation roots. The plan pins the exact signature before implementation.

Before effects, validate configuration, criteria/definition coverage, initial state/bindings, policy project, scope, renderer capability description and run identity. During assembly, bind each Situation's context, controller, limits, criteria and scope to this fixed request.

## Runtime collaborators

Reuse AudioAnalyzer, DecisionMaker, StateIntegrityInspector and AcceptanceJournal contracts.

Add a DiagnosisSource protocol for the controlled milestone. Its request contains selected state, scope and current observations, and its result is a DiagnosisReport or expected PortFailure. The loop calls validate_diagnosis_references. A labelled test double supplies reports now; real method selection remains explicit later.

Add an outer RunRenderer protocol accepting a request containing the validated RenderPlan, complete before state, explicit logical artifact root bindings and source/protection references. It encompasses preparation and one render call. The existing plan-only Renderer/GainMixProcessor interface remains the inner processing boundary. A later rendering adapter can compose its own workspace/root helpers and inner processor. The coordinator owns preflight and identity allocation before dispatch.

Add a runtime clock with monotonic seconds, UTC time and cancellation observation, and an identity source producing fresh typed IDs. These are injected Protocol collaborators in core.ports, with data-only return values. Reject nonfinite/decreasing elapsed observations. No global clock, hidden IDs or sleeps.

Run recording needs more than candidate acceptance. Extend the journal capability through a RunJournal protocol that includes the acceptance methods and ordered append/read-back of typed run events. The in-memory run journal composes the existing acceptance implementation within adapters.journal. Journal acceptance remains authoritative for selected reference. Trace append failure cannot retroactively undo an acceptance.

The implementation plan must pin all missing record/port signatures together in Task 1, including read-only journal head access for initialization binding. A coordinator must confirm it was given a journal initialized for its run and exact initial state at sequence zero. It cannot infer initialization from query absence.

## Trace

Use frozen typed events and one immutable ordered prefix. Event payloads contain concrete records/unions, with no Any/object/dict payload escape hatch. The trace retains:

- Initial request, mode NON_DURABLE, code/configuration provenance available at this milestone.
- Operation intent with run/call/attempt/execution identity, exact input, current accepted reference and projected charged usage.
- Observed result or expected failure, malformed response details where representable and explicit not-dispatched outcome.
- Each exact Situation, decision response, diagnosis, admission/preflight result, render result, receipt/candidate and verification request/result.
- Acceptance event/acknowledgement/query, integrity requests/reports and reconciliation.
- Compact DecisionFeedback built from each completed decision slot, rather than recursively embedding full traces in Situation.
- Terminal snapshot and separately observed returned completion.

Raw actual provider payloads and credentials are outside the controlled milestone. Later provider integration records real serialized requests/responses without changing the distinction between full trace and compact history.

Append events carry unique EventId and monotonic journal event sequence, independent of acceptance sequence. An acknowledgement must identify the exact stored event. Idempotent append accepts the same event once and rejects an ID conflict. Expected append failure or unknown acknowledgement prevents the corresponding effect. Never blindly dispatch or retry following unknown intent recording.

For a confirmed unwritten intent, projected charge is not applied. For an unknown intent acknowledgement, retain conservative consumed usage and mark dispatch unconfirmed, invoke no effect, and terminate for recording uncertainty. An acknowledged intent applies its charge before the second time/cancellation check. That second check may record NotDispatchedFeedback while retaining the charge.

## Serial run

1. Bind initial journal reference and inspect the initial complete state under the initialization deadline gate. Record evidence and terminate if integrity is failed/uncertain.
2. Analyze the initial state once, validate response and form StateEvidence.
3. Compute INITIAL verification. ACCEPTED/SATISFIED follows initial-success finalization. INCONCLUSIVE terminates. REJECTED checks that comparison baselines required for an attempt can be matched before proceeding.
4. Diagnose and assemble the capability view and exact Situation, with complete prior-call history. Before a decision dispatch, check and record intent for the projected charge. The supplied usage includes the current decision charge.
5. Validate the actual response. A structurally valid proposal charges one attempt before assess_decision or other action checking. Apply assess_decision, record all non-action outcomes and finish. An evidence-blocked action ends as insufficient evidence in the first version. An invalid response terminates as INVALID_DECISION_RESPONSE. No invented provider decision is used to summarize a transport failure.
6. For a proposal passing the gate, apply producer admission, authoritative advertised scope/renderer bound checks and preflight. Capability enforcement uses the current configured view and does not trust a provider-supplied capability reference. Rejected admission/preflight becomes feedback and may return to a new decision within budgets.
7. Reserve one recovery workflow before render. Allocate fresh execution, workspace token, candidate artifact/revision, checkpoint and verification identities. Build preflight from current recipe stem/mix bindings. Record a render intent and apply the render charge, recheck time/cancel, dispatch outer renderer once.
8. Validate candidate response, form ExecutionReceipt and derive candidate ProjectState with existing helpers. Update only the edited stem and combined mix bindings. Preserve fixed inputs. Root bindings for generated artifacts use the assigned workspace root key. Reuse compatible accepted observations; analyze a candidate once.
9. Compute ATTEMPT verification. Rejected candidates reconcile/inspect before; after verified retention and recorded recovery, another decision may follow within budget. Inconclusive candidate verification, analysis failure and render failure reconcile and terminate in the initial version.
10. Accepted verdicts produce a validated AcceptanceEvent. After a validated commit acknowledgement, advance the live accepted reference. Release the unused recovery reservation. Satisfied targets finish; intermediate improvement forms feedback and iterates within budget.
11. Definitely unwritten acceptance retains and checks before, then terminates for recording failure. Unknown acknowledgement spends the reserved recovery workflow on at most one authority query and at most one state inspection. Use reconcile_acceptance. Confirmed acceptance follows the original accepted route only after recording reconciliation. Confirmed absence reconciles before and terminates for recording failure. Unknown authority or failed/uncertain integrity yields the corresponding terminal reason.

The generation of required analysis/definition data is fixed input, not a policy the coordinator invents. If retained state observations are incompatible with a required definition, report the unavailable baseline rather than reanalyzing automatically.

## Budget, retries and cancellation

Use check_budget and explicit clock checkpoints. Budget counts, reservation and history usage are distinct facts. One serial edit has at most one outstanding recovery reservation. A render is blocked without unused recovery capacity. Reserving does not charge recovery; dispatching the recovery workflow does. A successful acceptance releases the reservation.

Recovery admission checks its own count and deadline, irrespective of exhausted decision/attempt/render counts. Recheck time/cancellation before each query and integrity call within recovery. An expired deadline can leave recovery unperformed and state integrity unknown.

Only expected DecisionMaker UNAVAILABLE failures with retryable=True can retry, within the configured per-round retry cap and global call/time limits. Each call consumes a new decision slot and appends actual failure feedback. No automatic analysis, render, recovery or journal retry. Controlled adapters have no hidden internal retries.

Cancellation and deadlines are cooperative boundaries, including after intent acknowledgement. A port that returns after an overrun has its actual outcome retained. No further ordinary work starts. Recovery/finalization follow the approved deadline policies.

## Completion

Keep RunOutcome's five members and FinalProjectState, adding the stable reason enum from section 27 in the runtime contracts task. Each implemented reason must have a reachable test. RunRecord's reason field migration is intentional and must preserve a human explanation separately.

RunReport includes the immutable trace, final RunRecord, known authoritative/verified references, full charged usage and explicit non_durable mode. Terminal snapshot records facts before its append. Returned completion records observed acknowledgement/failure and elapsed time after that append. Finalization is attempted after the ordinary-work deadline within the journal adapter's bounded-call contract.

Successful candidate verification alone cannot produce SUCCEEDED. Known selected reference, confirmed acceptance where applicable, successful required recording and deadline conditions must all support success. An accepted intermediate state remains retained when a later decision requests context or reaches a limit.

Unexpected programming errors propagate after a narrow finalization attempt preserves observed facts. They are never converted into normal PortFailure or successful RunReport.

## Ownership

New runtime, trace, diagnosis and outer render records live in core.records with acyclic dependencies. New Protocols live in core.ports. Coordinator and call/attempt/recovery helpers live in core.loop. Existing gates and verifier are composed rather than duplicated. Memory recording implementation belongs in adapters.journal. No core import of adapters or cross-adapter internal imports.

Split coordinator functions by lifecycle responsibility where needed. Use explicit typed state records for logical coordinator state and local mutable collections only as implementation details. Every completion yields immutable outputs.

## Acceptance scenarios and implementation order

Task 1 fixes runtime input, outer render/diagnosis, trace/recording, clock/identity, terminal and retry schemas, plus in-memory journal behavior. Task 2 connects the serial coordinator. Task 3 tests complete traces under controlled success/failure responses. Each task has exact file ownership and review checks in the subsequent implementation plan.

End-to-end scenarios trace to slides 5–9:

- Initial success without decision/render.
- One accepted correction and satisfied target.
- Accepted intermediate progress followed by another decision using the new state and prior feedback.
- Known rejected candidate, verified previous state and another bounded decision.
- Explicit context change between separately configured runs changes the controlled decision response; unknown context yields recorded questions. These test propagation/routing, not actual LLM reasoning.
- NoChange, insufficient evidence, conflicting constraints and unavailable capabilities.
- Stale or invalid analysis/decision/render responses and prior-history source preservation.
- Every operation limit, retry cap, cancellation and post-intent deadline block.
- Commit-before-lost-acknowledgement, confirmed non-commit, unresolved authority, candidate integrity failure/uncertainty, and failed reconciliation recording.
- Failed restoration, no recovery capacity and deadline-exhausted recovery.
- Finalization failure/overrun with truthful retained accepted edits and completion usage.

Temporary numerical WAV fixtures remain focused adapter tests. No artificial musical demo or performance result is claimed. Real musical testing follows owner-provided exports, justified measurements/methods and controller integration. Later project metadata, additional tools/families, durable recording and comparative experiments remain explicit milestones.
