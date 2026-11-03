# Migration plan: layered layout → ADR-027 hexagonal control-loop layout

Status: **Planned** (2026-11-03). Depends on [ADR-027](../decisions/0027-hexagonal-control-loop-architecture.md) (Proposed) and on the owner decisions in section 2. Rules for the target are in [`engineering-conventions.md`](../project/engineering-conventions.md).

## 1. Principles
1. **Behavior-preserving.** The migration only moves, renames and deletes re-export code. No field, validation, message or numerical behavior changes. New behavior (the control loop) starts only after the migration (section 6).
2. **Refactor first, then build the loop.** "Make the change easy, then make the easy change": mixing structural moves with new loop code makes both harder to review.
3. **One step, one commit, green after each.** After every step the full suite passes with the same number of behavior tests, `ruff check` passes and `autonomous-mixing --help` works.
4. **History-preserving moves.** Files move with `git mv` so `git log --follow` and blame keep working. Import rewriting is done by a one-off script kept outside the repository.
5. **No empty placeholders.** Packages without code (`diagnosis`, `decision_agent`, `baselines`, `experiments`, `evaluation`) are created only when their first real code arrives.
6. **One writer.** During the migration only one assistant (Claude Code or Codex) edits `src/` and `tests/`; the other works on documents or research only.

## 2. Owner decisions before starting

| ID | Decision needed | Recommendation |
| --- | --- | --- |
| MD-1 | Commit or discard the uncommitted ADR-026 processor work. It lives in `infrastructure/processing`, which this plan moves. | Commit it first, so the migration starts from a clean tree. |
| MD-2 | Accept ADR-027 with the two amendments below, or keep it as written. | Accept with amendments. |
| MD-3 | **Amendment A: no `core.measurement` and `core.decision` stage packages.** The request/response types each port exchanges, and the checks on them, live next to the port in `core.ports`. Stage packages may import `core.records` and `core.ports`. Reason: adapters must see the port's request/response types without importing a stage package. After moving, `measurement` and `decision` would hold only response checks, so as separate packages they add structure without content. | **Decided 2026-11-03: adopted.** The core becomes `records`, `ports`, `admission`, `verification`, `loop` (`diagnosis` later). |
| MD-4 | **Amendment B: migration order.** ADR-027 point 8 says to write the loop first. This plan moves the code first and builds the loop afterwards (principle 2). The loop-first benefit, finding unneeded contracts, comes from the contract review in section 5 instead. | **Decided 2026-11-03: adopted.** |
| MD-5 | Rename types to the new vocabulary in one dedicated step (M6), or keep the old names. | Rename in M6. |

MD-3 and MD-4 are reflected in ADR-027 (amended while still Proposed) and in `engineering-conventions.md`. MD-1, MD-2 and MD-5 are still open.

## 3. Target layout after migration
```
autonomous_mixing/
  core/
    records/          identifiers, invariants, project, artifacts, mix, context, observation, actions, decision
    ports/            results, analysis, decision, rendering (port protocols, their request/response types and checks)
    admission/        policy, admission, preflight
    verification/     criteria, result, consistency
    loop/             run, budgets, configuration, attempt, references, transitions, rollback
  adapters/
    rendering/        dsp, integrity, locations, workspaces, revalidation, wav_output, gain_mix
    dsp_analysis/     io, features, analysis  (M0 code; not yet an AudioAnalyzer)
  drivers/
    cli.py
```

## 4. Module mapping

### Domain → `core`
| Current | Target | Notes |
| --- | --- | --- |
| `domain/shared/identifiers.py`, `versions.py` | `core/records/identifiers.py` | merged: ids and `VersionRef` |
| `domain/shared/invariants.py` | `core/records/invariants.py` | the only `require_*` helpers; infrastructure copies removed in M4 |
| `domain/project/tracks.py`, `snapshot.py`, `state.py`, `processing.py`, `checkpoint.py` | `core/records/project.py` | one module for project state; `_require_unique_artifacts` kept once |
| `domain/project/artifacts.py` | `core/records/artifacts.py` | |
| `domain/project/mix.py`, `processing_spec.py` | `core/records/mix.py` | `MixRecipe`, `ProcessingSpec` and enums |
| `domain/project/scope.py` | `core/records/observation.py` | `AnalysisScope` describes what is measured; also used by run configuration and verification criteria |
| `domain/context/*` | `core/records/context.py` | |
| `domain/observation/*` | `core/records/observation.py` | `validate_observation_sources` → `core/ports/analysis.py` |
| `domain/action/proposal.py`, `operations.py` | `core/records/actions.py` | |
| `domain/action/execution.py` | split: receipts → `core/records/actions.py`; `derive_candidate_*`, `validate_gain_execution` → `core/loop/transitions.py` | `_require_unique_artifacts` duplicate removed |
| `domain/action/rollback.py` | `core/loop/rollback.py` | |
| `domain/decision/decision.py` | `core/records/decision.py` | |
| `domain/decision/references.py` | `core/ports/decision.py` | `validate_decision_references` |
| `domain/validation/*` | `core/verification/` | names change in M6 |
| `domain/run/*` | `core/loop/` | `Run`, attempts, budgets, configuration, references |
| `domain/__init__.py`, `domain/execution.py`, `domain/values.py` | deleted | compatibility facades |

### Application → `core`
| Current | Target | Notes |
| --- | --- | --- |
| `application/shared/results.py` | `core/ports/results.py` | `PortResult`, `PortFailure` |
| `application/observation/*` | `core/ports/analysis.py` | request, response, check, `AudioAnalyzer` |
| `application/decision/*` | `core/ports/decision.py` | request, response, check, `DecisionController`; `_require_unique_observations` kept once |
| `application/execution/identity.py`, `processing.py`, `validation.py`, `ports.py` | `core/ports/rendering.py` | plan, candidate, failure, check, `AudioProcessor` |
| `application/execution/requests.py`, `preflight.py` | `core/admission/preflight.py` | |
| `application/admission/*` | `core/admission/` | |
| `application/__init__.py`, `contracts.py`, `results.py`, `execution_contracts.py`, `execution_preflight.py`, `ports/` | deleted | compatibility facades |

### Infrastructure, M0 and CLI → `adapters` / `drivers`
| Current | Target | Notes |
| --- | --- | --- |
| `infrastructure/audio/dsp.py` | `adapters/rendering/dsp.py` | |
| `infrastructure/audio/verification.py` | `adapters/rendering/integrity.py` | renamed: "verification" is reserved for the loop |
| `infrastructure/filesystem/locations.py` | `adapters/rendering/locations.py` | |
| `infrastructure/workspaces/allocation.py`, `validation.py` | `adapters/rendering/workspaces.py`, `revalidation.py` | |
| `infrastructure/wav_output/writing.py` | `adapters/rendering/wav_output.py` | |
| `infrastructure/processing/gain_mix.py` | `adapters/rendering/gain_mix.py` | |
| `audio/io.py`, `features.py`, `analysis.py` | `adapters/dsp_analysis/` | kick/bass deviation (ADR-006) unchanged |
| `cli.py` | `drivers/cli.py` | `pyproject.toml` entry point → `autonomous_mixing.drivers.cli:main`; needs `uv sync` |

Open in M4: the future `dsp_analysis` analyzer also needs integrity-checked WAV reading. Before that code exists, decide between a shared adapter-support module and a public integrity function in `adapters/rendering`. This decision is not needed for the migration itself.

## 5. Contract review (loop walkthrough on paper)
The loop from ADR-027 was walked through against the existing contracts. **Nothing is removed during the migration**; this table only marks what the loop work in section 6 should confirm or simplify.

| Contract | Loop use | Verdict |
| --- | --- | --- |
| `AnalysisRequest` / `AnalysisResponse` / `AudioAnalyzer` | measure | keep |
| `DecisionRequest` | what the decision maker sees | keep → rename `Situation` (M6). Gap: no iteration history or problem candidates yet (FR-06) |
| `DecisionResponse` | wraps exactly one `Decision` | candidate to drop (port could return `PortResult[Decision]`) |
| `ActionAdmissionRequest`, `admit_action`, policy | admit | keep |
| `ExecutionRequest` + `preflight_execution` | metadata preflight → plan | keep; `ExecutionRequest` partly duplicates `ActionAdmissionRequest` (both carry the decision and policy): candidate to merge |
| `ProcessingPlan` / `ProcessedCandidate` / `ProcessingFailure` / `AudioProcessor` | render | keep → `RenderPlan`, `RenderedCandidate`, `RenderFailure`, `Renderer` (M6) |
| `ExecutionReceipt` | render outcome record | overlaps `ProcessedCandidate`: candidate to merge |
| `derive_candidate_project_state`, `validate_gain_execution`, `ArtifactCheckpoint` | state transition | keep |
| `RollbackReceipt` | roll back | with immutable states, rollback means keeping the previous state; candidate to simplify to a recorded disposition |
| `ValidationCriteria` / `ValidationResult` | verify | keep → `AcceptanceCriteria`, `VerificationResult` (M6); no verifier exists yet |
| `Run`, `CorrectionAttempt`, budgets, configuration | history, budgets, run record | keep → `RunRecord` (M6) |
| `PortResult` / `PortFailure` | all ports | keep |

## 6. Steps

| Step | Content | Exit criteria |
| --- | --- | --- |
| **M0 prerequisites** | MD-1 to MD-5 decided; ADR-027 amended if needed; record the baseline (`uv run pytest -q` count, currently 2,893 passed / 3 skipped per research log). | Clean working tree, baseline recorded in the research log. |
| **M1 records** | Create `core/records`; move the domain record modules per section 4; rewrite imports in `src` and `tests`; delete `domain/execution.py`, `domain/values.py` and the `domain` root facade. | Same behavior-test count passes; no `autonomous_mixing.domain.*` import left outside packages still to be moved. |
| **M2 ports** | Create `core/ports`; move application `shared`, `observation`, `decision` and execution port/plan/candidate modules, plus `validate_observation_sources` and `validate_decision_references`. | As M1. |
| **M3 admission, verification, loop** | Create `core/admission` (admission + preflight + requests), `core/verification` (domain validation) and `core/loop` (run records, rollback, transitions). Delete the empty `domain` and `application` packages and their facades. | As M1; `domain` and `application` no longer exist. |
| **M4 adapters and drivers** | Move `infrastructure/*` into `adapters/rendering`, M0 `audio/` into `adapters/dsp_analysis`, `cli.py` into `drivers/`; update the `pyproject.toml` entry point and run `uv sync`; replace the infrastructure copies of `require_text`/`require_instance` with `core.records.invariants` where messages stay identical (otherwise keep them and note why). | As M1; `uv run autonomous-mixing --help` works from the new entry point. |
| **M5 enforcement and tests** | Replace `test_domain_component_boundaries.py`, `test_application_infrastructure_boundaries.py` and both `*_compatibility.py` tests with one `tests/test_architecture.py`: the four import rules, no cycles, and the immutability check (every record class is a frozen dataclass with only immutable field annotations). Reorganize tests to mirror `src` (`tests/core/…`, `tests/adapters/…`, `tests/drivers/…`). | Behavior-test count unchanged; removed boundary/compatibility tests listed in the commit message and research log. |
| **M6 vocabulary and slots** | Rename types to the ADR-027 vocabulary (`Validation*` → `Acceptance*`/`Verification*`, `AudioProcessor` → `Renderer`, `ProcessingPlan` → `RenderPlan`, `DecisionController` → `DecisionMaker`, `DecisionRequest` → `Situation`, `Run` → `RunRecord`); add `slots=True` to all records. | As M1; repository-wide search finds no old names in `src`. |
| **M7 documentation** | Rewrite the implemented sections of `overview.md` for the new layout; update `CLAUDE.md` State, the ADR index and the research log with evidence (test counts per step). | Docs name the new paths only; superseded statements marked explicitly. |

After M7, the control-loop work (roadmap M4 vertical slice, FR-06, FR-07, FR-09, FR-12) starts in `core/loop` with test stand-ins for the three ports and settles the "candidate" rows in section 5.

## 7. Effort and schedule
- M1 to M4 are mechanical (moves and import rewriting), about half a day each. M5 and M6 take about a day each, M7 half a day. Total **about 3 to 4 working days**.
- The roadmap places M3 (Claude adapter) on Nov 2–8 and M4 (vertical slice) on Nov 9–22. The migration uses most of the remaining M3 week. Starting the Claude adapter in the old layout would mean moving it again, so the adapter should start after M4 of this plan at the latest. Whether the roadmap dates change is an owner decision; this plan does not change the roadmap.

## 8. Risks
| Risk | Mitigation |
| --- | --- |
| Import rewriting misses dynamic or string imports (for example `importlib` in the boundary tests) | Those tests are replaced in M5; until then, run the full suite after each step. |
| Merging modules (for example `project.py`) changes definition order or `__module__`, which some tests inspect | Compatibility tests that check `__module__` are removed in M5; behavior tests do not depend on it. If an earlier step fails only on such checks, record that in the commit. |
| Parallel edits by the second assistant | Principle 6: one writer during the migration. |
| Scope creep ("while we are here, also fix…") | Every non-move change found during the migration becomes a note for section 6, not part of a migration commit. |
