# Migration plan: layered layout → ADR-027 hexagonal control-loop layout

Status: **Planned**, revised 2026-11-03 after an independent Codex review. Depends on [ADR-027](../decisions/0027-hexagonal-control-loop-architecture.md) (Proposed) and on the owner decisions in section 2. Rules for the target are in [`engineering-conventions.md`](../project/engineering-conventions.md).

Revised again after Codex's second review (2026-11-03): data scope narrowed to core-facing records, `budgets` split out to avoid a module cycle, MD-6 replaced with the exact reproduced case. Superseded from the first version of this plan: the claim that every step is behavior-preserving, the exit criterion "same behavior-test count" as sole evidence, the mapping that put budgets and run records in `core.loop` (it created a forbidden ports → loop dependency), and the 3-to-4-day estimate as a planning fact.

## 1. Principles
1. **Classified steps.** Each step belongs to exactly one class, named in its commit:
   - **(a) move:** preserves operational behavior; only module locations and import statements change.
   - **(b) API break:** deletes old import paths (compatibility facades and shims). Owner-approved, deliberately breaking.
   - **(c) rename:** vocabulary changes of public names. Optional.
   - **(d) slots:** adds `slots=True` with its own compatibility tests. Optional.

   Only (a) and (b) are prerequisites for the control loop.
2. **Refactor first, then build the loop.** The loop starts after the (a) and (b) steps; it decides which contracts are unnecessary (section 5).
3. **Architecture coverage is evidence, not a side effect.** Old architecture tests are updated in the same commit as each move, and the guarantee ledger (section 6) states for every old assertion its replacement or why it was dropped. A green behavior-test count alone is not evidence of equal coverage.
4. **History-preserving moves.** Files move with `git mv`; import rewriting uses a one-off script kept outside the repository.
5. **No empty placeholders.** `diagnosis`, `decision_agent`, `baselines`, `experiments` and `evaluation` are created only with their first real code.
6. **One writer.** During the migration only one assistant edits `src/` and `tests/`.
7. **Pilot first.** Step M1 is a pilot. Its measured effort and any problems decide whether the rest proceeds as planned.

## 2. Owner decisions

| ID | Decision | Status |
| --- | --- | --- |
| MD-1 | Commit the ADR-026 processor work before migrating. | Done 2026-11-03 (`a13d196`). |
| MD-2 | Accept ADR-027. | Accepted by the owner 2026-11-03 after two Codex reviews; the M1 pilot still decides whether the remaining steps proceed as planned. |
| MD-3 | No `core.measurement` or `core.decision` packages. | Adopted 2026-11-03; narrowed by the review: request/response types are records, ports own protocols and response checks. |
| MD-4 | Move code first, build the loop afterwards. | Adopted 2026-11-03. |
| MD-5 | Vocabulary renames as optional class (c) step. | Decided 2026-11-03: renames are done, in step M5 after the moves and the API break, using the rename table in section 7a. |
| MD-6 | Disposition of the ADR-026 processor/caller location gap (below). | Decided 2026-11-03: option 1, implemented before M1 as its own behavior change (ADR-026 amended). Independent re-review pending. |

**MD-6 case (from the Codex architecture audit of 2026-11-03, reproduced there against real temporary files).** A relative input location can equal the processor's planned relative output location while resolving under a different configured root; for example, a fixed input recorded as `<directory_token>/target.wav` under the source root, with a separately allocated output root. Resolver, bound inventory and processor support this layout, the files are physically distinct and no source bytes are overwritten. The processor renders and returns a `ProcessedCandidate`, but the caller's `validate_processed_candidate` then rejects it, because it compares output and input location strings lexically without root context ("output locations must differ … (lexical check)", `application/execution/validation.py`). It is an integration-contract gap, not a DSP failure or an unsafe overwrite. Options:
1. Bounded guard (audit recommendation): during the processor's no-I/O binding, compute the two prospective output location strings and reject equality with any location the caller validator checks, before verification, DSP or writing; add a regression with equal relative strings under different physical roots; document the conservative supported-layout restriction. Keeps the current API and the fail-closed lexical contract.
2. Root-aware location identity: support such cross-root layouts through a separately designed location/context abstraction. Larger; no path resolution may be imported into core validation to work around it.

The adapter was committed in `a13d196` (MD-1) before this re-review. Option 1 is implemented as `_require_distinct_output_locations` in `infrastructure/processing/gain_mix.py`, called during binding before input lookup; ADR-026 remains Proposed until that change is re-reviewed.

## 3. Target layout
```
autonomous_mixing/
  core/
    records/        core-facing business and port records, and the invariant checks records call
                    in __post_init__ (adapter-local data stays in its adapter)
      identifiers, invariants, artifacts, project, mix, context, observation, actions,
      budgets, results, decision, analysis, verification, rendering, admission, run
    ports/          protocols and response checks: analysis, decision, rendering
    admission/      admit_action, preflight_execution
    verification/   reference consistency checks (the verifier itself is loop work)
    loop/           state transitions; run() is loop work
  adapters/
    rendering/      dsp, integrity, locations, workspaces, revalidation, wav_output, gain_mix
    dsp_analysis/   io, features, analysis (M0 code; not yet an AudioAnalyzer)
  drivers/
    cli.py
```
Placement rule for functions: a check that a record calls in its `__post_init__` stays with the records (records may not import ports, stages or the loop). Everything else that is behavior moves out.

Scope rule for data: only core-facing business and port records move to `core/records`. Adapter-local implementation data stays in its adapter, for example `VerifiedAudio` (holds a NumPy array), `ResolvedArtifactLocation`, `WorkspaceRoot`, `WorkspaceAllocationRequest`, `AllocatedWorkspace`, `WrittenWav` and `ProcessingLimits`.

Module graph inside `core/records`, derived from the current imports (each module imports only modules listed before it; the M1 pilot checks it with the architecture test):

| Module | Imports from records |
| --- | --- |
| `invariants` | — |
| `identifiers` | invariants |
| `artifacts` | identifiers, invariants |
| `project` | artifacts |
| `mix` | identifiers |
| `context` | project |
| `observation` | artifacts, project |
| `actions` | artifacts, project |
| `budgets` | identifiers, invariants |
| `results` | identifiers, invariants |
| `decision` | actions, budgets, context, observation, project |
| `analysis` | observation, project |
| `verification` | observation, project |
| `rendering` | actions, artifacts, mix, observation, project |
| `admission` | actions, decision, mix, observation, rendering |
| `run` | actions, budgets, context, decision, observation, project, verification |

`budgets` is a module of its own because `decision` (`DecisionRequest`) needs it and `run` (`RunConfiguration`, which needs `ControllerIdentity` from `decision`) needs it too; putting budgets into `run` would create the cycle decision → run → decision.

## 4. Module mapping

### Data and record invariants → `core/records`
| Current | Target module |
| --- | --- |
| `domain/shared/identifiers.py`, `versions.py`, `invariants.py` | `identifiers`, `invariants` |
| `domain/project/*` (tracks, snapshot, state, processing, checkpoint, artifacts, mix, processing_spec, scope) | `project`, `artifacts`, `mix`, `observation` (scope) |
| `domain/context/*` | `context` |
| `domain/observation/*` incl. `validate_observation_sources` (called by `DecisionRequest.__post_init__`) | `observation` |
| `domain/action/proposal.py`, `operations.py` | `actions` |
| `domain/action/execution.py`: `ExecutionStatus`, `ExecutionIssue`, `ExecutionReceipt`, `validate_receipt_against_checkpoint` and `derive_candidate_checkpoint` (both called by `CorrectionAttempt.__post_init__`; the latter found in the M1 pilot) | `actions` |
| `domain/action/rollback.py`: `RollbackStatus`, `RollbackReceipt`, `validate_rollback_references` (called by `CorrectionAttempt.__post_init__`) | `actions` |
| `domain/decision/decision.py` | `decision` |
| `domain/validation/criteria.py`, `result.py` | `verification` |
| `domain/run/budgets.py` | `budgets` |
| `domain/run/configuration.py`, `run.py`, `attempt.py` | `run` |
| `application/shared/results.py` | `results` |
| `application/observation/contracts.py` | `analysis` |
| `application/decision/contracts.py` | `decision` |
| `application/admission/policy.py`, `contracts.py`; `application/execution/requests.py`; preflight result types from `preflight.py` | `admission` |
| `application/execution/identity.py`, `processing.py` | `rendering` |

### Behavior → `core/ports`, stages, `core/loop`
| Current | Target |
| --- | --- |
| `application/observation/ports.py`, `validation.py` | `core/ports/analysis.py` |
| `application/decision/ports.py`, `validation.py`; `domain/decision/references.py` | `core/ports/decision.py` |
| `application/execution/ports.py`, `validation.py` | `core/ports/rendering.py` |
| `application/admission/admission.py`; `preflight_execution` | `core/admission/` |
| `domain/validation/consistency.py` | `core/verification/` |
| `domain/action/execution.py`: `derive_candidate_project_state`, `validate_gain_execution` | `core/loop/transitions.py` |
| `domain/run/references.py` | `core/loop/references.py` |

`validate_gain_execution` is also called by `derive_candidate_*` (same target module) and `domain/run/references.py` (also loop). If the M1 pilot finds a record that calls a function mapped here, that function moves to `core/records` instead, and the ledger notes it.

### Deleted in the class (b) step
`domain/__init__.py` root exports, `domain/execution.py`, `domain/values.py`, `application/__init__.py` root exports, `application/contracts.py`, `results.py`, `execution_contracts.py`, `execution_preflight.py`, `application/ports/`. Until then, during class (a) moves, the old package paths re-export from the new locations so that unrelated tests keep importing.

### Infrastructure, M0 and CLI → `adapters`, `drivers`
| Current | Target | Notes |
| --- | --- | --- |
| `infrastructure/audio/dsp.py` | `adapters/rendering/dsp.py` | |
| `infrastructure/audio/verification.py` | `adapters/rendering/integrity.py` | "verification" is reserved for the loop |
| `infrastructure/filesystem/locations.py` | `adapters/rendering/locations.py` | |
| `infrastructure/workspaces/allocation.py`, `validation.py` | `adapters/rendering/workspaces.py`, `revalidation.py` | |
| `infrastructure/wav_output/writing.py` | `adapters/rendering/wav_output.py` | |
| `infrastructure/processing/gain_mix.py` | `adapters/rendering/gain_mix.py` | MD-6 gap unchanged by the move |
| `audio/*` | `adapters/dsp_analysis/` | kick/bass deviation (ADR-006) unchanged |
| `cli.py` | `drivers/cli.py` | entry point `autonomous_mixing.drivers.cli:main`; needs `uv sync` |

Open for later: the future analyzer also needs integrity-checked WAV reading; decide then between a shared adapter-support module and a public function in `adapters/rendering`.

## 5. Contract review (loop walkthrough on paper)
Nothing is removed during the migration. These rows are questions for the loop work.

| Contract | Loop use | Question for the loop work |
| --- | --- | --- |
| `AnalysisRequest` / `AnalysisResponse` / `AudioAnalyzer` | measure | keep |
| `DecisionRequest` | what the decision maker sees | rename `Situation` (class c); lacks iteration history and problem candidates required by FR-06. The actual request must be captured and persisted with each attempt (FR-12, QR-03) |
| `DecisionResponse` | wraps exactly one `Decision` | could the port return `PortResult[Decision]`? |
| `ActionAdmissionRequest`, `admit_action`, policy | admit | keep |
| `ExecutionRequest` + `preflight_execution` | metadata preflight → plan | `ExecutionRequest` partly duplicates `ActionAdmissionRequest` (both carry the decision and policy): merge? |
| `ProcessingPlan` / `ProcessedCandidate` / `ProcessingFailure` / `AudioProcessor` | render | keep; rename in class (c) |
| `ExecutionReceipt` | render outcome record | overlaps `ProcessedCandidate`: merge? |
| `derive_candidate_project_state`, `validate_gain_execution`, `ArtifactCheckpoint` | state transition | keep |
| `RollbackReceipt` | roll back | review. Immutable states keep the previous state available, but rollback still needs retained artifacts that pass integrity checks, correct selection of the last accepted reference and handling of uncertain outcomes |
| `ValidationCriteria` / `ValidationResult` | verify | keep; no verifier exists yet |
| `Run`, `CorrectionAttempt`, budgets, configuration | history, budgets, run record | keep; persistence and provenance not decided |
| `PortResult` / `PortFailure` | all ports | keep |

## 6. Guarantee ledger
Maintained in this section during the migration. Every assertion of the four old architecture tests (`test_domain_component_boundaries.py`, `test_domain_component_compatibility.py`, `test_application_infrastructure_boundaries.py`, `test_application_component_compatibility.py`) is assigned to one row; each step updates the "status" column for the rows it touches.

| ID | Old guarantee | New form | Status |
| --- | --- | --- | --- |
| G1 | Layer direction: domain imports no application, third-party or M0 code; application imports no infrastructure | Rules (a)–(c) of ADR-027 in `tests/architecture/` | M1: records rule (b) in `tests/architecture/test_core_records.py`; old domain/application checkers extended to admit `core.records` as a target (no other rule relaxed) M2: rules (a) and (b) for the whole core in `tests/architecture/test_core_layers.py`; old allow lists widened from `core.records` to `core` (no other rule relaxed). |
| G2 | Component dependency matrices and acyclic component graph | Dropped: matrices (ADR-027 rationale). Kept: acyclic module graph | M1: unchanged (old matrices still enforced for domain and application) |
| G3 | Import-checker robustness: relative, aliased, nested-init imports; look-alike packages; unresolved and unclassified modules; recursive discovery | Kept: the existing checker helper moves into `tests/architecture/` with its fixture tests | M1: partial. The new records test uses a small resolver (absolute and relative imports); the old checkers and their fixture tests are unchanged and still run. Moving the full checker is due by M4 |
| G4 | Runtime load isolation in fresh processes (domain loads no third-party code; infrastructure leaves load no M0/CLI) | Kept for `core` (loads no NumPy, soundfile, provider SDK). Per-leaf rendering isolation: decided in M4, default dropped with reason | M1: `core.records` load test added; old runtime-load tests extended to admit `core` and `core.records` M2: importing every core module loads only standard-library and `core` modules. |
| G5 | Fresh-process imports succeed in every order without partial initialization | Kept | M1: unchanged; passes alone and in most full runs, but failed intermittently in full runs both before M1 (baseline) and after it; cause not determined |
| G6 | Cross-component imports go through package interfaces, not internals | Kept for adapters (rule c); inside `core`, decided in the M1 pilot | M1 decision: inside `core.records`, modules import sibling modules directly (never the `records` interface), which keeps the module graph explicit and checkable; enforced by `test_the_records_interface_imports_only_records_modules` and the planned-graph test M2: the `ports`, `admission`, `verification` and `loop` initializers contain only a docstring, so every behavior name has one import path. |
| G7 | Legacy paths and facades re-export identical objects; root exports unchanged | Kept until the class (b) step, then dropped (deliberate API break) | M1: kept; every old module path still re-exports the moved objects (shims or package interfaces). M1 correction (Codex review): `ExecutionIssue` was missing from `domain.action.execution`, and 9 moved private helpers were missing from their legacy modules; all restored as explicit re-exports, and the compatibility test no longer accepts a missing attribute (it had used `getattr(module, name, obj)`) M2: legacy modules of moved behavior are complete shims, including 5 more private helpers and the M1 aliases they carried; the legacy audit against `495c123` finds nothing missing. |
| G8 | Constructor, function and protocol signatures equal the 218c3dc baseline | Kept through class (a) moves as move-equivalence evidence (old name → new path); retired or rebased in class (c) | M1: unchanged and passing; the signature baselines are checked through the legacy paths, which now resolve to the moved objects |
| G9 | Every public definition defined exactly once, inventory complete | Kept | M1: for moved records, exact ownership moved to `test_every_public_record_is_defined_exactly_once_in_its_listed_module` (explicit inventory, AST definition sites); old compatibility tests now assert identity through legacy paths for moved names and exact ownership only for names not yet moved M2: moved functions are listed in a behavior inventory and must be defined exactly once in `core`. |
| G10 | Exact permitted edges of filesystem, audio, workspaces, wav_output, processing | Decided in M4: kept as internal rules of `adapters/rendering`, or dropped with reason | planned |
| G11 | (new) Core records are frozen dataclasses with only immutable field types and no arrays; no dataclass is defined in `core` outside `core/records`; the `core/records` module graph is acyclic. Adapter-local dataclasses are out of scope | New in M1 | M1: implemented in `tests/architecture/test_core_records.py`; inherited exception: `Success[T].value` is an unconstrained generic payload, so a frozen `Success` can hold a mutable value. It is listed explicitly, and a test fails if any other record gains an unconstrained (TypeVar, `Any`, `object`) field |
| G12 | (gap) SR-10 runtime inputs and configuration contain no hidden-data fields | No test exists today. Not created by the migration; required separately (ADR-027 point 4) | open |

## 7. Steps

| Step | Class | Content | Exit criteria |
| --- | --- | --- | --- |
| M0 | — | MD-2, MD-5, MD-6 decided; MD-6 disposition implemented and re-reviewed if option 1 is chosen; baseline recorded (2,893 passed, 3 skipped on 2026-11-03, `.venv`, full suite). | Clean tree; baseline in the research log. |
| **M1 pilot** | (a) | Create `tests/architecture/` (checker moved, rules for `core.records`, G11). Move all data types and record invariants into `core/records` per section 4; old `domain` and `application` package paths re-export from the new locations. Update the old architecture tests for the moved modules in the same commit. | Behavior tests: same count. Ledger rows G1, G3, G6, G8, G9, G11 updated. Effort measured and recorded; go/no-go decision by the owner. |
| M2 | (a) | Move behavior into `core/ports`, `core/admission`, `core/verification`, `core/loop`. | As M1; ledger updated. |
| M3 | (a) | Move `infrastructure/*`, `audio/*`, `cli.py` into `adapters` and `drivers`; entry point and `uv sync`; decide G4 and G10. | As M1; `autonomous-mixing --help` works. |
| M4 | (b) | Delete all compatibility facades, shims and the empty `domain`, `application`, `infrastructure` packages; remove the convenience re-exports of `core/records/__init__.py` so every record has exactly one import path, its module (owner decision 2026-11-03); retire the four old architecture tests; ledger complete. | Behavior tests: same count; every ledger row has a final status. |
| M5 | (c) | Optional: vocabulary renames. | No old names in `src`; G8 rebased. |
| M6 | (d) | Optional: `slots=True` with tests for attribute assignment, pickling and `dataclasses.replace`. | As M1. |
| M7 | — | Documentation: `overview.md` implemented sections, `CLAUDE.md` State, ADR index, research log with evidence per step. | Docs name new paths only; superseded statements marked. |

After M4 (or M7 if run), the control loop (roadmap M4 vertical slice; FR-06, FR-07, FR-09, FR-12) starts in `core/loop` with test stand-ins for the three ports, and answers the questions in section 5.

## 7a. Rename table (step M5, MD-5)
Only Python identifiers are renamed. String values that can appear in records, evidence or reports stay unchanged: enum values, failure-code strings such as `input_verification_failed`, error messages and `VersionRef` names such as `autonomous_mixing.gain_mix_processor`. Each rename is a plain identifier change with no alias left behind; G8 is rebased in the same commit. Renames are applied symbol-aware (AST or IDE refactoring), not by text replacement: `Run` also occurs as an ordinary word, for example in `cli.py`. All new names were checked to be unused in `src` and `tests` on 2026-11-03.

| Current | New | Reason |
| --- | --- | --- |
| `AudioProcessor` | `Renderer` | port name (ADR-027 point 3) |
| `ProcessingPlan` | `RenderPlan` | "processing" names four different things today |
| `ProcessedCandidate` | `RenderedCandidate` | as above |
| `ProcessingFailure` | `RenderFailure` | as above |
| `ProcessingOutcome` | `RenderOutcome` | as above |
| `ArtifactProcessingEvidence` | `RenderEvidence` | as above |
| `validate_processed_candidate` | `validate_rendered_candidate` | as above |
| `DecisionController` | `DecisionMaker` | port name (ADR-027 point 3) |
| `DecisionRequest` | `Situation` | what the decision maker sees |
| `ValidationCriteria` | `AcceptanceCriteria` | "validation" means well-formedness only |
| `ValidationResult` | `VerificationResult` | runtime accept or roll back is verification |
| `ValidationResultId` | `VerificationResultId` | as above |
| `ValidationVerdict` | `Verdict` | as above |
| `validate_validation_references` | `validate_verification_references` | as above |
| `Run` | `RunRecord` | a record, not an active run |
| `ArtifactVerificationError` | `IntegrityError` | "verification" is reserved for the loop |
| `VerificationFailureCode` | `IntegrityFailureCode` | as above |
| `VerifiedAudio` | `CheckedAudio` | as above |
| `verify_wav_artifact` | `check_wav_integrity` | as above |
| `verify_resolved_wav_artifact` | `check_resolved_wav_integrity` | as above |

Not renamed: `AudioAnalyzer`, `ProcessingSpec` (a configuration record that names processing semantics, not the render step), `ExecutionReceipt`, `ExecutionStatus`, `ExecutionIssue`, `DecisionResponse` and `RollbackReceipt`; the loop work in section 5 may merge or remove several of them, so renaming them first would be wasted churn. Admission names (`ActionAdmissionPolicy`, `AdmittedAction`, `RejectedAction`) already fit the vocabulary.

## 8. Effort and schedule
- No estimate is treated as established until the M1 pilot has been measured. The earlier 3-to-4-day guess is withdrawn as a planning fact.
- Steps M5 and M6 are optional and do not block feature work.
- The roadmap places M3 (Claude adapter) on Nov 2–8 and M4 (vertical slice) on Nov 9–22. Whether those dates move is an owner decision after the pilot; this plan does not change the roadmap.

## 9. Risks
| Risk | Mitigation |
| --- | --- |
| A record calls a function mapped to a behavior package, creating a records → stage dependency | Placement rule in section 3; the pilot checks every `__post_init__` call before moving. |
| Import rewriting misses dynamic or string imports (`importlib`, fresh-process tests) | Full suite after each step; G3/G5 tests run in the same commit. |
| Old architecture tests reject the new package names | Updated in the same commit as each move (principle 3). |
| Coverage silently shrinks | Guarantee ledger; no old architecture test is deleted before its rows have a new form or a recorded drop reason. |
| Parallel edits by the second assistant | One writer (principle 6). |
| Scope creep | Non-move findings become notes for the loop work, not migration commits. |
