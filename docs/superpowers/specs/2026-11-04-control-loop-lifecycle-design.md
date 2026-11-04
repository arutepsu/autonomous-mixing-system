# Control-loop lifecycle: source requirements and design brief

Date: 2026-11-04
Status: source lifecycle confirmed by the owner; implementation contracts remain under design.
Repository baseline inspected: `6daba81` on `refactor/adr-027-m1`.

## Authority and use

The owner confirmed that the OBSERVE / DIAGNOSE / PLAN–DECIDE / ACT / EVALUATE / ITERATE–ROLLBACK lifecycle comes from the project's source material and must remain a standing constraint. The supplied architecture/lifecycle diagrams and this confirmation are the basis of this brief. The wording is not a newly introduced feature proposal.

Every subsequent control-loop design, implementation prompt and review must cite this brief together with ADR-027, engineering-conventions.md and the relevant requirements. Folder names alone do not establish that the lifecycle has been implemented.

Read alongside:
- `docs/architecture/adr-027-migration-plan.md`, section 5: contract questions.
- `docs/decisions/0027-hexagonal-control-loop-architecture.md`.
- `docs/project/engineering-conventions.md`.
- `docs/project/requirements.md`: FR-05–FR-12, FR-17, QR-03–QR-04, SR-10 and OP-04.

## Fixed lifecycle

1. **OBSERVE:** audio measurements, project state, supplied musical context, previous actions and feedback from previous attempts.
2. **DIAGNOSE:** likely mixing problems, such as masking, frequency buildup or phase interaction, with their supporting evidence and uncertainty. A measurement is not automatically a perceptual diagnosis.
3. **PLAN / DECIDE:** choose a justified intervention, or No Change. The intended action families are EQ, gain, dynamic processing and stereo adjustment. Diagnostic confidence and action confidence are distinct.
4. **Enough evidence and context?** Only a justified, permitted action may proceed. Otherwise abstain or request specific missing context; never guess it.
5. **ACT:** apply one bounded modification through defined tools. Deterministic admission and metadata preflight precede rendering. Rendering produces tentative candidate audio.
6. **EVALUATE:** render, re-analyze and determine whether the target improved sufficiently and whether other metrics worsened. Criteria are declared before the attempt; the decision maker does not choose its own success rule afterwards.
7. **STOP / ITERATE / ROLLBACK:** stop on verified sufficiency; retain a qualifying intermediate improvement and iterate where allowed; restore the last accepted reference after a rejected attempt. Feed the recorded outcome into the next observation. Resource limits and failures also terminate explicitly.

The agent is more than a prompt: LLM + observations + state + tools + constraints + feedback loop. The deterministic loop owns orchestration and enforcement; the AI decision component proposes.

### Evaluation terminology

EVALUATE is the source diagram's label for the runtime assessment step. In code and technical documentation its name is **verification**, in `core.verification`. The `evaluation` package means post-run research evaluation against hidden reference/ground truth and remains unavailable to the runtime loop (SR-10). Keeping the diagram's label does not authorize a runtime dependency on hidden data.

### Acceptance semantics

Passing guard checks is necessary for accepting a candidate. Meeting the target permits success; a minimum improvement is not additionally required once the target is satisfied. If targets remain unmet, accepting intermediate progress requires the configured minimum-improvement checks to pass. Missing required evidence cannot establish acceptance. The exact handling of each non-accepted assessment must be specified in the contract walkthrough.

No Change, insufficient evidence, missing context and budget exhaustion have different meanings. A decision maker's No Change statement alone does not establish deterministic success. No numeric confidence calibration, scientific threshold or family-specific detector is invented by this design brief.

## Existing implementation and boundaries

| Lifecycle responsibility | Existing owner / next work |
| --- | --- |
| Measurement contract | `core.ports.analysis`; records in `core.records.analysis` and `observation`. The M0 analyzer is not yet a complete generic implementation of this port. |
| Problem candidates | Planned `core.diagnosis`; representation and scientifically justified methods are unresolved (FR-05, D11). |
| Decision input / output | `Situation`, `Decision`, `DecisionResponse`, `DecisionMaker`. History and candidates need contract review. |
| Permission and preflight | `core.admission`; retain these responsibilities in ACT. |
| Candidate rendering | `Renderer`, `RenderPlan`, `RenderedCandidate`; concrete gain/mix rendering in `adapters.rendering`. |
| Verdict computation | `core.verification` has consistency checks and records; the measurement-comparison verifier remains to be implemented. |
| State derivation | `core.loop.transitions` and `references` contain checks and derivation helpers, not a working run loop. |
| History, termination, budgets | Existing records are groundwork; enforcement, full traces and persistence remain to be designed. |

Only gain rendering currently exists. The lifecycle must support explicit capabilities; mentioning EQ, dynamics or stereo in the conceptual design does not make them executable. Kick/bass may illustrate a contract, but is not a hardcoded system model.

`core.loop` coordinates pure stages and ports. Audio arrays, paths, provider SDKs, file operations and workspace allocation remain in adapters/drivers according to ADR-027. New ports or changes to existing ones require a concrete lifecycle need.

## First design deliverable

Walk through one complete correction attempt on paper, tracking:
- immutable initial state, last accepted state and tentative candidate;
- the exact observations, context, candidates and history seen by the decision maker;
- action permission, preflight, rendering and receipt creation;
- candidate measurements and deterministic verification;
- state selection, integrity evidence, history and counters on every exit.

Then wrap this attempt in bounded repetition. Start implementation with controlled test doubles for the ports; numerical examples in tests are not research evaluation results. Connecting real analysis, files and a provider is a separately reviewed integration step.

## Decisions still open

These are design questions, not permission for an implementer to silently choose an API:
1. What history, problem candidates and separate uncertainty information belong in `Situation`? How is the actual decision input captured?
2. How are No Change, Abstain and Request Context represented? Owner decision on 2026-11-04: a context request ends the run; supplied answers start a new run with updated context. There is no pause/resume in the first version. The proposed representation is in `2026-11-04-control-loop-attempt-contract.md`, Task 2.
3. Keep `DecisionResponse`, or return `PortResult[Decision]`? Decide from the walkthrough, not wrapper count.
4. Keep or merge admission/preflight requests? Preserve the distinction between a proposal, permission and readiness.
5. How does `RenderedCandidate` become a linked `ExecutionReceipt`? What distinct evidence does each record carry?
6. How are per-attempt identities and workspaces supplied without filesystem operations in the core, given that `GainMixProcessor` is bound to one allocated workspace?
7. What evidence proves restoration, and what happens when rendering or restoration has an uncertain outcome? Keeping an old Python object is insufficient proof of audio integrity.
8. Which observations are compared, with what compatible scope/settings, baseline, target, improvement and guard precedence?
9. When are counters charged, what retry limits apply, and how is recovery handled when an ordinary budget is exhausted?
10. What trace is returned, what must be persisted, and how are provider provenance and raw requests/responses captured without credentials or hidden data?

## Completion evidence

A completed design must specify behavior for already-satisfactory input, accepted improvement, successful completion, regression, insufficient improvement, unavailable evidence, abstention, missing context, disallowed action, malformed port responses, expected port failure, uncertain execution, restoration failure and every budget limit. It must state which initial implementation scenarios use test doubles and which requirements remain incomplete until real integration/persistence exists.
