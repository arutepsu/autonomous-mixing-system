# Control Loop Diagnosis Records Implementation Plan (Increment 2A)

> **For agentic workers:** Use superpowers:executing-plans serially. Implement this increment only, test first, and stop with an uncommitted diff for Codex review. Preserve unrelated work; the owner commits after review.

**Goal:** Provide immutable evidence-linked problem-candidate and diagnosis-coverage records, with structural reference validation, for a later `Situation` extension.

**Architecture:** All new data and the cross-record check that future record constructors will call live in `core.records.diagnosis`; new identifiers live in `core.records.identifiers`. This is a lower-level dependency for decision records. No detector, diagnosis behavior package, provider call, policy gate or `Situation` change is implemented here.

**Tech stack:** Existing Python >=3.11, stdlib, pytest, Ruff and uv. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-04-control-loop-lifecycle-design.md` and `docs/superpowers/specs/2026-11-04-control-loop-attempt-contract.md`, sections 11–12, 16–17 and 28. Parent design approved on 2026-11-04. Budget gate reviewed and committed; inspected HEAD `77a925d`. If HEAD advances, inspect the scope instead of resetting it.

## Global constraints

- Preserve the source lifecycle and Diagnostic confidence ≠ Action confidence. These records describe diagnosis evidence; they do not justify or authorize an action.
- No scientific method, detection threshold, grade-to-probability mapping, calibrated confidence, defect injection, DSP algorithm or negative diagnosis is invented.
- D11 remains open for the scientifically justified uncertainty schema/method. The records below are an explicitly qualitative/uncalibrated descriptive envelope, not an approved calibration method.
- Core records are frozen, typed and validated. Collections are tuples; constructors reject wrong collection types rather than normalizing them. Follow existing `require_*` conventions and ValueError behavior.
- Keep the records import graph acyclic; packages remain docstring-only. Do not add root exports or compatibility aliases.
- No change to existing decisions, Situation, admission, ports, run records, budget records, adapters, CLI, dependency files, accepted ADRs or requirement statuses.
- Masking, frequency buildup and phase interaction are family labels only. Kick/bass is not a built-in track model.
- The reference validator checks structure/provenance, not the scientific truth or perceptual validity of a candidate.

## Review focus

1. An empty candidate tuple must not conflate assessed coverage with unavailable or incomplete diagnosis.
2. A candidate tied to one coverage row must not borrow a different method, family, project or revision, or escape its declared scope.
3. Superseded artifact evidence must be rejected; unchanged artifacts with older production revisions remain legitimate current members.
4. Unknown/duplicate evidence IDs and observations from a different state must not silently pass; unreferenced extras are not treated as candidate support.
5. Incomplete or wholly unavailable support must remain explicitly insufficient; no numerical score can accidentally be interpreted as calibrated certainty.

## Ownership

- Modify `src/autonomous_mixing/core/records/identifiers.py`: add `ProblemCandidateId` and `DiagnosisCoverageId` using the existing `_Identifier` pattern; preserve every existing definition.
- Create `src/autonomous_mixing/core/records/diagnosis.py`: the eight public names below, plus private invariant helpers if needed.
- Create `tests/unit/test_diagnosis_records.py`.
- Modify `tests/architecture/test_core_records.py`: add the two identifiers; add `diagnosis` to `ALLOWED_RECORD_IMPORTS` with allowed siblings exactly `identifiers`, `invariants`, `observation`, `project`; add its eight public names to `INVENTORY`. No existing edge or test is relaxed/retired.
- Modify `docs/architecture/overview.md`: describe the records and reference check; explicitly say no detector or Situation integration exists yet.
- Modify `docs/research/research-log.md`: append one dated evidence/limitation entry; earlier entries stay unchanged.
- Update this implementation plan's checkboxes only.

No `core.diagnosis` package is created. `test_core_layers.py` needs no behavior-inventory change because the new check belongs with records and will be called by later record construction. Stop/report before touching an unowned path for a material conflict.

## Task 1: Diagnosis metadata and reference contract

### Enums and exact values

`ProblemFamily(Enum)`:
- `MASKING = "masking"`
- `FREQUENCY_BUILDUP = "frequency_buildup"`
- `PHASE_INTERACTION = "phase_interaction"`

`UncertaintyRepresentation(Enum)`:
- `QUALITATIVE = "qualitative"`
- `UNCALIBRATED = "uncalibrated"`

`DiagnosisCoverageStatus(Enum)`:
- `ASSESSED = "assessed"`
- `UNAVAILABLE = "unavailable"`
- `INCOMPLETE = "incomplete"`

Representation and insufficiency are separate facts. A qualitative description can be insufficient; an uncalibrated description is not automatically sufficient. There is no calibrated enum member or numeric probability field.

### Frozen records: all fields required, no defaults

```python
DiagnosticEvidence(
    strength: str,
    uncertainty: str,
    representation: UncertaintyRepresentation,
    insufficient_evidence: bool,
    limitations: tuple[str, ...],
    missing_information: tuple[str, ...],
)

DiagnosisCoverage(
    coverage_id: DiagnosisCoverageId,
    family: ProblemFamily,
    scope: AnalysisScope,
    status: DiagnosisCoverageStatus,
    method: VersionRef | None,
    explanation: str,
)

ProblemCandidate(
    candidate_id: ProblemCandidateId,
    coverage_id: DiagnosisCoverageId,
    project_id: ProjectId,
    revision: Revision,
    family: ProblemFamily,
    scope: AnalysisScope,
    evidence_ids: tuple[ObservationId, ...],
    method: VersionRef,
    evidence: DiagnosticEvidence,
)

DiagnosisReport(
    project_id: ProjectId,
    revision: Revision,
    coverage: tuple[DiagnosisCoverage, ...],
    candidates: tuple[ProblemCandidate, ...],
)
```

The eight public names in `diagnosis.py` are the three enums, four records and `validate_diagnosis_references`. Identifiers are owned only by `identifiers.py`.

### Constructor invariants

Type-check every field. Text must be non-blank; preserve it exactly rather than trimming/coercing. `insufficient_evidence` must be bool, not integer/string substitutes. Text tuples must be tuples of distinct non-blank strings; empty tuples are permitted except `missing_information` must be nonempty when `insufficient_evidence` is True. Do not derive scientific strength from that boolean or require particular words in free text.

Coverage:
- ASSESSED and INCOMPLETE require a typed method/version.
- UNAVAILABLE permits method None or a typed method that could not be used; explanation is always required.
- A row describes coverage for that particular family/method/scope, not the whole project.

Candidates:
- `evidence_ids` is nonempty, typed and distinct.
- Scope's `track_ids` are the affected tracks. Do not add a second divergent track list.
- A standalone candidate constructor cannot check external project membership or evidence availability; those checks occur below.

Reports:
- `coverage` is nonempty, typed; `candidates` is typed and may be empty.
- Coverage IDs are unique. Reject duplicate `(family, scope, method)` coverage keys even if their IDs/statuses differ. Distinct methods may describe the same family/region under distinct rows.
- Candidate IDs are unique. Every candidate matches report project/revision, names an existing coverage ID, and has the same family and method as that row.
- No candidate may use UNAVAILABLE coverage. INCOMPLETE coverage permits only candidates marked `insufficient_evidence=True`. ASSESSED coverage can still describe uncertain/insufficient hypotheses; it asserts the method returned a report, not calibrated certainty.
- Candidate scope must be contained in its own coverage scope: tracks are a subset, time interval is contained, and if the outer scope restricts frequency the candidate needs an explicit contained band. An unrestricted outer band allows either candidate form. Use exact comparisons; preserve supplied ordering. No sorting, clamping or tolerance.

Report construction checks only embedded records, not actual method execution or measurement truth. ASSESSED plus no candidates is not an automatic all-clear. UNAVAILABLE/INCOMPLETE remain distinct even when candidates are empty.

### Reference check

```python
def validate_diagnosis_references(
    report: DiagnosisReport,
    *,
    state: ProjectState,
    scope: AnalysisScope,
    observations: Iterable[Observation],
) -> None:
    ...
```

`Iterable` is from `collections.abc`. The function:
1. Type-checks report, state and scope, and rejects a non-iterable observations argument with ValueError. Individual observations must have their proper type. Exceptions raised by an iterator itself propagate unchanged; do not add a catch-all.
2. Requires report project/revision to equal the selected state. Validates supplied scope against `state.snapshot`.
3. Requires every coverage scope to be within the supplied scope using the same containment rule as above; all referenced tracks must be known. Candidate scopes also validate against the snapshot. Report construction already binds candidates to their coverage rows.
4. Consumes the observations iterable once into an ID lookup. Rejects duplicate IDs anywhere, even identical duplicate records. Type/ID uniqueness checks also apply to unreferenced extras.
5. Requires every candidate evidence ID to exist. Validates each referenced observation's sources using existing `validate_observation_sources(observation, state.audio.artifacts, state.audio, state.snapshot)`; repeated citation of the same ID across different candidates is allowed and need only be checked once.
6. If all of one candidate's referenced observations have `Unavailable` results, that candidate must have `insufficient_evidence=True`. Partial measurement availability alone does not establish scientific sufficiency.
7. Returns None. No checks of protected tracks, permissions, action suitability, thresholds, candidate probability, numerical results or actual detector execution are added.

Unreferenced observations are ignored after type/ID checks, matching existing reference-validator policy. Do not invent further observation-to-candidate time/frequency/track-union rules: different future methods may combine inputs differently. Those semantics need approved method contracts. Existing source provenance validation remains mandatory for cited evidence.

## Step 1: Read and write failing constructor tests

- [x] Read both parent specs, this whole plan, CLAUDE/conventions and existing identifier/observation/source-validation patterns. Record HEAD/status; preserve unrelated changes.
- [x] Write metadata-only tests for new identifiers, exact enums/field shapes, frozen behavior, every constructor invariant, and valid reports. Run the focused file before implementation; observe missing-name/module failure.
- [x] Implement the two identifier types and new records, updating the exact records inventory/graph. Existing identifiers and signatures remain unchanged.
- [x] Run focused constructor and records-architecture tests; require exit 0. Do not create a detector implementation or change Situation.

## Step 2: Write failing reference tests, then the validator

- [x] Add tests for the missing reference function and its entire contract before its implementation. Constructor-valid fixtures must reach each intended external-reference check.
- [x] Cover at least:
  - Correct empty ASSESSED report, empty UNAVAILABLE report, empty INCOMPLETE report and mixed family/region coverage remain distinct.
  - Wrong report/candidate project/revision, missing coverage ID, family/method disagreement, duplicate coverage/candidate IDs and duplicate coverage keys.
  - Candidate containment boundaries: exact time/band equality passes; outside tracks/time/band fails; explicit band required under a restricted parent. A candidate may cover a smaller region than its parent row.
  - UNAVAILABLE coverage cannot carry candidates; INCOMPLETE needs insufficient evidence with a stated missing-information item.
  - Real bool requirement, immutable text tuples, blank and duplicate strings, wrong types and frozen records.
  - Reference success using a generator; unknown cited ID; duplicate IDs including unreferenced extras; wrong source/state; coverage outside the supplied run scope; unknown tracks.
  - A cited superseded artifact is rejected. A retained current member produced at an older revision is accepted. Do not restore the production-revision equality bug.
  - All cited measurements Unavailable requires explicit insufficiency. A partial evidence set is not automatically classified as sufficient or insufficient by the validator.
  - Unreferenced valid extras are ignored; a non-iterable or wrong observation type raises ValueError. An iterator RuntimeError propagates as the same object.
- [x] Implement `validate_diagnosis_references` and private scope helpers, using only stdlib and allowed records siblings. Run focused and architecture tests; require exit 0.
- [x] Temporary mutation probes: remove the report/state revision check; allow candidates under UNAVAILABLE coverage; omit current-checkpoint source validation. Each must fail an appropriate test. Restore files and rerun focused tests. No mutation dependency/tooling is added.

## Step 3: Documentation, checks and handoff

- [x] Update the overview in place, and append a research-log entry. State that these are descriptive contracts/reference checks only. No scientific method, calibrated confidence, actual detector, action gate, new Situation field or completed FR-05/FR-06 claim follows.
- [x] Run:

```text
uv run --locked pytest tests/unit/test_diagnosis_records.py -q
uv run --locked pytest tests/architecture tests/unit/test_public_contracts.py -q
uv run --locked pytest -q -rs
uv run --locked ruff check .
uv run --locked ruff format --check src tests
uv run --locked autonomous-mixing --help
uv lock --check
```

- [x] Inspect the exact diff: only owned paths, no earlier log edits, no initializer/export changes, old record definitions untouched and dependency files unchanged. If uv genuinely cannot be found, use existing repository .venv equivalents and report the missing lock check without installing anything.
- [x] Stop uncommitted. Handoff exact paths/interfaces, red/green and mutation evidence, observed verification/skips, deviations and gaps. Do not add feedback/action-capability records, No Change, Situation integration or the next increment.

## After review

Next separately planned slices are the lower-level action-capability and feedback records, then Situation/outcome integration. Their exact fields and consumers will be defined before coding; this task does not authorize guessing them.
