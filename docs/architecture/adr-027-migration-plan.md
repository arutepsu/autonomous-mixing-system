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
| MD-2 | Accept ADR-027. | Open. Codex review: approve the direction conditionally; this plan makes approval of the remaining steps depend on the M1 pilot. |
| MD-3 | No `core.measurement` or `core.decision` packages. | Adopted 2026-11-03; narrowed by the review: request/response types are records, ports own protocols and response checks. |
| MD-4 | Move code first, build the loop afterwards. | Adopted 2026-11-03. |
| MD-5 | Vocabulary renames as optional class (c) step. | Open. |
| MD-6 | Disposition of the ADR-026 processor/caller location gap (below). | Open. Recommendation: option 1, as its own behavior-change commit with ADR-026 re-review, **before M1**, so that the class (a) moves carry no pending behavior change. |

**MD-6 case (from the Codex architecture audit of 2026-11-03, reproduced there against real temporary files).** A relative input location can equal the processor's planned relative output location while resolving under a different configured root; for example, a fixed input recorded as `<directory_token>/target.wav` under the source root, with a separately allocated output root. Resolver, bound inventory and processor support this layout, the files are physically distinct and no source bytes are overwritten. The processor renders and returns a `ProcessedCandidate`, but the caller's `validate_processed_candidate` then rejects it, because it compares output and input location strings lexically without root context ("output locations must differ … (lexical check)", `application/execution/validation.py`). It is an integration-contract gap, not a DSP failure or an unsafe overwrite. Options:
1. Bounded guard (audit recommendation): during the processor's no-I/O binding, compute the two prospective output location strings and reject equality with any location the caller validator checks, before verification, DSP or writing; add a regression with equal relative strings under different physical roots; document the conservative supported-layout restriction. Keeps the current API and the fail-closed lexical contract.
2. Root-aware location identity: support such cross-root layouts through a separately designed location/context abstraction. Larger; no path resolution may be imported into core validation to work around it.

The adapter was committed in `a13d196` (MD-1) before this re-review; ADR-026 remains Proposed until the disposition is implemented and re-reviewed.

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
| `identifiers`, `invariants` | — |
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
| `admission` | decision, mix, observation, rendering |
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
| `domain/action/execution.py`: `ExecutionStatus`, `ExecutionIssue`, `ExecutionReceipt`, `validate_receipt_against_checkpoint` (called by `CorrectionAttempt.__post_init__`) | `actions` |
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
| `domain/action/execution.py`: `derive_candidate_checkpoint`, `derive_candidate_project_state`, `validate_gain_execution` | `core/loop/transitions.py` |
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
| G1 | Layer direction: domain imports no application, third-party or M0 code; application imports no infrastructure | Rules (a)–(c) of ADR-027 in `tests/architecture/` | planned |
| G2 | Component dependency matrices and acyclic component graph | Dropped: matrices (ADR-027 rationale). Kept: acyclic module graph | planned |
| G3 | Import-checker robustness: relative, aliased, nested-init imports; look-alike packages; unresolved and unclassified modules; recursive discovery | Kept: the existing checker helper moves into `tests/architecture/` with its fixture tests | planned |
| G4 | Runtime load isolation in fresh processes (domain loads no third-party code; infrastructure leaves load no M0/CLI) | Kept for `core` (loads no NumPy, soundfile, provider SDK). Per-leaf rendering isolation: decided in M4, default dropped with reason | planned |
| G5 | Fresh-process imports succeed in every order without partial initialization | Kept | planned |
| G6 | Cross-component imports go through package interfaces, not internals | Kept for adapters (rule c); inside `core`, decided in the M1 pilot | planned |
| G7 | Legacy paths and facades re-export identical objects; root exports unchanged | Kept until the class (b) step, then dropped (deliberate API break) | planned |
| G8 | Constructor, function and protocol signatures equal the 218c3dc baseline | Kept through class (a) moves as move-equivalence evidence (old name → new path); retired or rebased in class (c) | planned |
| G9 | Every public definition defined exactly once, inventory complete | Kept | planned |
| G10 | Exact permitted edges of filesystem, audio, workspaces, wav_output, processing | Decided in M4: kept as internal rules of `adapters/rendering`, or dropped with reason | planned |
| G11 | (new) Core records are frozen dataclasses with only immutable field types and no arrays; no dataclass is defined in `core` outside `core/records`; the `core/records` module graph is acyclic. Adapter-local dataclasses are out of scope | New in M1 | planned |
| G12 | (gap) SR-10 runtime inputs and configuration contain no hidden-data fields | No test exists today. Not created by the migration; required separately (ADR-027 point 4) | open |

## 7. Steps

| Step | Class | Content | Exit criteria |
| --- | --- | --- | --- |
| M0 | — | MD-2, MD-5, MD-6 decided; MD-6 disposition implemented and re-reviewed if option 1 is chosen; baseline recorded (2,893 passed, 3 skipped on 2026-11-03, `.venv`, full suite). | Clean tree; baseline in the research log. |
| **M1 pilot** | (a) | Create `tests/architecture/` (checker moved, rules for `core.records`, G11). Move all data types and record invariants into `core/records` per section 4; old `domain` and `application` package paths re-export from the new locations. Update the old architecture tests for the moved modules in the same commit. | Behavior tests: same count. Ledger rows G1, G3, G6, G8, G9, G11 updated. Effort measured and recorded; go/no-go decision by the owner. |
| M2 | (a) | Move behavior into `core/ports`, `core/admission`, `core/verification`, `core/loop`. | As M1; ledger updated. |
| M3 | (a) | Move `infrastructure/*`, `audio/*`, `cli.py` into `adapters` and `drivers`; entry point and `uv sync`; decide G4 and G10. | As M1; `autonomous-mixing --help` works. |
| M4 | (b) | Delete all compatibility facades, shims and the empty `domain`, `application`, `infrastructure` packages; retire the four old architecture tests; ledger complete. | Behavior tests: same count; every ledger row has a final status. |
| M5 | (c) | Optional: vocabulary renames. | No old names in `src`; G8 rebased. |
| M6 | (d) | Optional: `slots=True` with tests for attribute assignment, pickling and `dataclasses.replace`. | As M1. |
| M7 | — | Documentation: `overview.md` implemented sections, `CLAUDE.md` State, ADR index, research log with evidence per step. | Docs name new paths only; superseded statements marked. |

After M4 (or M7 if run), the control loop (roadmap M4 vertical slice; FR-06, FR-07, FR-09, FR-12) starts in `core/loop` with test stand-ins for the three ports, and answers the questions in section 5.

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
