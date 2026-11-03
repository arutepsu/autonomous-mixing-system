# Research log
## 2026-10-14
- Repository starter created.
- M0 descriptive analysis only; masking metric pending literature and calibration.

## 2026-10-14 — repository preparation
- Named project `autonomous-mixing-agent`; Python import package remains `mixing_agent`.
- Adopted uv conventions and prepared structured documentation for Claude collaboration.
- Existing analyzer behavior remains unchanged; uv lock resolution and tests on a freshly synced uv environment still need local verification.

## 2026-10-14 — environment and M0 workflow verification
- Follows up the open item from the previous entry: `uv lock` resolved 31 packages (including the optional `agent` extra, which is locked but not installed by default); `uv sync --group dev` installed 14 packages on CPython 3.11.17; `uv lock --check` and `uv sync --locked` pass. uv 0.13.1 was run from a temporary virtual environment because uv is not yet installed on the development machine's PATH.
- Test suite before changes: 5 passed. After adding loader and CLI tests: 17 passed. `ruff check .` clean with the project-pinned rule set.
- Synthetic stems regenerated with `scripts/generate_test_audio.py`; the CLI report is identical to the previously stored `results/analysis.json` (same SHA-256 input hashes and feature values), so the synthetic generator and analysis are deterministic on this machine.
- Observed values on synthetic stems (descriptive only, no perceptual meaning): kick RMS −18.66 dBFS / peak −3.33 dBFS; bass RMS −19.25 dBFS / peak −14.90 dBFS; temporal coactivity fraction 0.164.
- Invalid-input checks previously surfaced as Python tracebacks (missing file reported only as "System error"). The CLI now reports validation errors as one-line messages with exit code 2; mismatch messages include the offending sample rates / frame counts. Analysis results are unchanged.
- The project directory is not yet a Git repository; no commit has been made.

## 2026-10-15 — naming migration to `autonomous-mixing-system`
- Supersedes the naming note of the 2026-10-14 "repository preparation" entry; see ADR-005. Import package renamed `mixing_agent` → `autonomous_mixing`, CLI `mixing-agent` → `autonomous-mixing`, distribution `autonomous-mixing-agent` → `autonomous-mixing-system`. Module contents unchanged apart from the CLI program name.
- Verification (uv 0.13.1, again from a temporary virtual environment; CPython 3.11.17): `uv sync --group dev` replaced the editable install; `uv lock --check` passes (lockfile changed only in the project name); 17 tests pass; `ruff check .` clean; `mixing_agent` is no longer importable; the `autonomous-mixing` entry point resolves to `autonomous_mixing.cli:main`.
- Regenerated synthetic stems and the CLI report are byte-identical to those from before the migration (same SHA-256), so analysis behavior is unchanged.
- GitHub repository renamed to `arutepsu/autonomous-mixing-system` and `origin` updated; `git ls-remote origin` succeeds. The local working directory is renamed separately by the owner.

## 2026-10-15 — research-first development policy; migration re-validation
- Owner policy correction recorded as ADR-006. Correction to the previous entry: regenerating the synthetic stems and re-running the CLI on them was a demo run, not a required validation step; it is not repeated here.
- The project `.venv` was stale after the working-directory rename: the editable install still pointed to `...\autonomous-mixing-agent\src` and console-script trampolines failed ("uv trampoline failed to canonicalize script path"), so imports and `pytest` failed. Fixed with `uv sync --group dev --locked --reinstall` (uv 0.13.1 from a temporary virtual environment; uv is still not on PATH).
- Validation after re-sync (CPython 3.11.17): `uv lock --check` passes (lockfile diff vs. last commit: project name only); all `autonomous_mixing` modules import; `mixing_agent` is not importable; distribution `autonomous-mixing-system` 0.1.0 registers `autonomous-mixing = autonomous_mixing.cli:main`; `autonomous-mixing --help` exits 0; 17 tests pass; `ruff check .` clean.
- Demo-asset review (nothing deleted): `scripts/generate_test_audio.py` (tracked) is not used by any test, as the tests synthesize their own signals; `data/synthetic/kick.wav`, `data/synthetic/bass.wav` and `results/analysis.json` are untracked, gitignored outputs of earlier demo runs. Disposition pending owner approval (see `architecture.md` open questions).
- Known ADR-006 deviations in M0 code, retained unchanged: kick/bass-named inputs (CLI, `analyze_pair`, report keys) and hardcoded 30–200 Hz band and 10 % activity threshold.

## 2026-10-15 — documentation restructured into topic folders
- Implemented ADR-007: `docs/` now has `decisions/` (one file per ADR plus index), `architecture/overview.md`, `research/` and `project/`. Files were moved with `git mv`; ADR-001 to ADR-006 were copied verbatim from `decisions.md` (heading level only). Paths in earlier entries of this log refer to the previous flat layout.
- Links in `README.md`, `CLAUDE.md` and the living documents were updated; all relative Markdown links were checked to resolve. No code or behavior changed.

## 2026-10-15 — Requirements Specification v1.0
- Researcher approved the consolidated Requirements Specification v1.0 (`docs/project/requirements.md`), replacing the unapproved v1 draft. Requirements are stated technology-neutrally; no architectural alternative (state representation, class structure, orchestration framework) was decided.
- Main changes: multitrack project input (FR-01); compatibility and alignment required, with the resampling/alignment policy left open (FR-02, D3); uncertainty representable without assuming calibrated confidence (FR-05, D11); action types and bounds deferred (FR-08, D5); per-family depth for P1–P3 (FR-13); new comparison, evaluation, provenance and safety requirements (FR-15 to FR-18, QR-07, QR-08, SR-01 to SR-09, OP-01 to OP-05). SR-01 requires each comparison to name its independent variables and document confounders; equal action budgets alone are not treated as sufficient.
- Corrected the overstated M0 status (WAVEX/RF64 were marked "tested" without a test). Under the v1.0 wording the M0 exit criteria are not met (multitrack input, full configuration recording and numeric feature tests are missing).
- Thesis title corrected to "Autonomous Detection and Correction of Mixing Problems Using an AI Agent" in `README.md` and `project-scope.md`.
- Scope clarified: P1, P2 and P3 remain planned research targets; P1 end-to-end is the contingency minimum, not a decision to abandon P2/P3 (`project-scope.md`, roadmap priority 3).
- ADR-008 clarifies ADR-006 point 6: controlled defect injection into real stems is permitted; artificially generated music is not evaluation data.
- Documentation only; no code, tests or audio changed.

## 2026-10-15 — development agent team aligned with the offline WAV baseline
- Added five development-assistant role definitions (mixing-coordinator, mixing-engineer, mixing-dsp, mixing-evaluator, thesis-supporter) for Claude Code (`.claude/agents/`) and Codex (`.codex/agents/`), plus `agent-team/PROJECT_BRIEF.md` and `agent-team/README.md`. They are assistants for thesis development, not part of the runtime system (ADR-004 unaffected).
- The prepared configuration followed the Source of Truth document v0.1, which proposes an FL Studio prototype with an early FL Studio feasibility milestone and conflicts with ADR-001, `requirements.md` (FR-14 withdrawn) and `project-scope.md`. Owner resolved this on 2026-10-15; recorded as ADR-009: the offline Python/WAV workflow is the implementation baseline, and FL Studio integration is deferred and optional. ADR-001 and the reference documents are unchanged.
- Remaining differences between the document and the repository stay open and are listed in `agent-team/PROJECT_BRIEF.md`: hypotheses (context sensitivity, abstention), comparison design versus B0–B3 (D7), context-priority cases, P2/P3 depth, a hidden-reference requirement not yet stated in `requirements.md`, and milestone numbering.
- Verification: structural checks of all ten definitions passed (required fields; matching Claude/Codex text). Runtime discovery by either client has not been confirmed. Configuration only; no code, tests or audio changed.

## 2026-10-15 — hidden-reference boundary documented; brief references restored
- Recorded the hidden-reference boundary as ADR-010 and requirement SR-10 (requirements v1.1) at the owner's instruction. Original reference audio and defect-injection ground truth are unavailable to every controller, its decision inputs and runtime validation; they are stored separately and joined with run records only after execution. The ordinary action history remains runtime information.
- Correction to the earlier requirements wording: FR-18 had propagated defect-injection parameters into run records; it now keeps them out and links them only by identifiers. FR-12, the system-context inputs/outputs, `dataset-plan.md` (leakage prevention), `architecture/overview.md` (target pipeline), `agent-team/PROJECT_BRIEF.md` and the mixing-evaluator role (Claude and Codex) were aligned. ADR-008 is refined, not edited (index status updated); the hidden-reference item ADR-009 listed as open is closed.
- `agent-team/PROJECT_BRIEF.md`: the three reference-document paths, which had been removed, were restored after verifying the files exist at their original locations; no repository copies exist.
- Documentation and configuration only; no code, tests or audio changed. Other Source of Truth conflicts remain open.

## 2026-10-19 — domain foundation records (replacement Task 1)
- Added `autonomous_mixing.domain` (values, project, context, observation): immutable, standard-library-only records with construction-time invariants, following the owner-approved mixing-core design and its repository adaptation (replacement Task 1). Proposed as ADR-011. Groundwork for FR-01/02, FR-03/04, FR-11, FR-12, QR-03/08 only; no requirement status changed. M0 code, CLI, report schema and existing tests unchanged; the new types are not integrated into M0.
- Test-first: `tests/unit/test_domain_foundation.py` failed at collection before implementation (module absent). The first green run exposed two faulty test helpers that substituted defaults for empty/`None` inputs; after fixing them, 98 tests passed. A manual mutation check (disabling the bool/non-finite rejection, and separately the scope-coverage check) made the focused tests fail; the files were restored byte-identically.
- Verification (CPython 3.11.17, pytest 9.1.1, ruff 0.17.0): baseline before changes was 17 passed, `ruff check .` clean, `--help` exit 0, `uv lock --check` passes; after changes, 115 passed, `ruff check .` clean, scoped `ruff format --check` clean, `--help` exit 0, `import autonomous_mixing.domain` succeeds.
- Tooling note: uv is still not on PATH; uv 0.13.1 (matching `.venv/pyvenv.cfg`) was run from a temporary virtual environment outside the repository, with `uv run --locked`. No dependency changes.

## 2026-10-20 — domain foundation milestone (Tasks 1–2D) and provenance correction
- Completed the dependency-free domain layer `autonomous_mixing.domain` in reviewed steps: project, context and observation records (Task 1, ADR-011); typed action proposals and single-outcome decisions (2A, ADR-012); validation criteria and result records (2B, ADR-013); run configuration, lifecycle and budget records (2C, ADR-014); correction attempts, artifact checkpoints and execution/rollback receipts (2D, ADR-015). All ADRs remain Proposed. Owner-requested corrections during review: dependency-boundary checker resolves relative imports (Task 1); ATTEMPT acceptance requires passing guards and either SATISFIED targets or UNMET targets with passing improvements (2B); run-scope band must not exceed render Nyquist (2C); embedded attempt validation must be ATTEMPT mode (2D).
- Milestone review findings: (1) checkpoints required every member artifact's production revision to equal the checkpoint revision, so a later state could not retain unchanged artifacts without retagging provenance; (2) integers beyond the float range raised OverflowError instead of ValueError, including in derived artifact end times and Nyquist values; (3) a candidate artifact could reuse a checkpoint artifact ID.
- Corrections (owner-approved 2026-10-20): provenance (`AudioArtifact.revision`) is separated from state membership (`ArtifactCheckpoint`); `ExecutionReceipt` carries explicit `superseded_artifact_ids`; `derive_candidate_checkpoint` builds candidate membership without accepting it; candidate IDs must not reuse before-state IDs; `validate_observation_sources` requires an explicit checkpoint and identical recorded metadata; overflow becomes ValueError at construction. Replacement is never inferred; dependency invalidation of derived artifacts is future execution-planning work. ADR-011 and ADR-015 amended before acceptance.
- Verification: see the milestone commit; tests use metadata fixtures and synthetic numbers only. No audio was generated or processed, and no requirement status changed. M0 code, CLI, dependencies and source audio unchanged.

## 2026-10-20 — Task 3A committed; domain reorganized into component packages
- Task 3A (application observation and decision contracts, ADR-016) committed as `66f5fba` after re-verification (716 tests passing).
- Structural refactor following the owner-approved design and migration plan, proposed as ADR-017: the flat domain modules became eight component packages (`shared`, `project`, `context`, `observation`, `action`, `decision`, `validation`, `run`) with explicit interfaces and an acyclic dependency matrix. `ArtifactCheckpoint` moved from the former `execution` module to `project`; receipts, rollback and candidate derivation to `action`. Definitions were copied verbatim from the original sources; the root exports and the `values`/`execution` modules remain compatibility re-exports of the same objects.
- Evidence of behavior preservation: a before/after dump of all 76 root exports and 83 public objects (dataclass fields, defaults, bases, methods, enum members and values, function signatures) is identical apart from module paths; identity tests confirm legacy, root and component imports name the same objects. Test-only architecture checks enforce the matrix, public-interface access and compatibility exceptions; injected violations were detected.
- No behavior, requirement status, dependency, M0 code or audio changed. Refactor left uncommitted for review.

## 2026-10-21 — fixed-input gain semantics and composite project state (ADR-019)
- Implemented the owner-approved processing-state specification and migration plan, proposed as ADR-019. `SetTrackGain` now means absolute additional gain relative to an explicitly fixed runtime input stem, never a delta on the latest output; a missing gain state never means 0 dB. New `domain.project` records `TrackGainState`, `ProjectProcessingState` and `ProjectState` pair settings with snapshot and audio membership; `domain.action.derive_candidate_project_state` assembles candidate states purely.
- Intentional contract changes: `CorrectionAttempt` uses `before_state` plus a `candidate_state` required exactly for CANDIDATE_READY execution (also when PENDING); `RollbackReceipt` records `restored_state`, and VERIFIED requires exact equality with the saved before state including its snapshot timestamp; analysis and decision requests carry one `state` instead of separate snapshot and checkpoint fields. Admission decisions and provenance checks are unchanged.
- Evidence is structural: gain sequences 0 → -3 → -3 → -6 → 0 are metadata transitions with distinct fixture outputs, not audio tests. No audio was generated, rendered or read, and no requirement status changed. M0 code, CLI, dependencies and source audio unchanged. Left uncommitted for review.

## 2026-10-21 — offline execution contracts and metadata preflight (ADR-020)
- Implemented the first stage of the owner-approved offline execution design and plan, proposed as ADR-020: a versioned `MixRecipe` (`SummingPolicy.SAMPLE_SUM`, ordered logical contributors) in `domain.project`; execution request records (`StemBinding`, `ExecutionWorkspace`, `CandidateIdentity`, `ExecutionRequest`); a pure `preflight_execution` returning `MetadataReady` with a `ProcessingPlan` or a typed `PreflightRejected`; the `AudioProcessor` Protocol; and structural checks of processor outputs and evidence.
- Preflight reruns admission and then checks workspace, candidate identity, full-recipe bindings, the selected full mix, explicit target gain state, compatible metadata and undeclared target-dependent outputs, in that order. The plan separates the fixed target input from already-rendered current stems, so non-target gains are never applied twice.
- Evidence is structural only: metadata fixtures and in-memory processor doubles. No DSP, file I/O, hashing, workspace allocation, locks, execution service or new dependency was added; `MetadataReady` proves no file integrity, alignment, current authorization or workspace safety. No requirement status changed. Left uncommitted for review.

## 2026-10-21 — pre-DSP audit corrections F1 and F5
- A read-only architecture review of `7313ffc` found that a correction record could claim a target gain change while the receipt replaced only the mix, leaving the old target stem as current audio. It still derived an ACCEPTED, reference-valid attempt (F1). It also found that a candidate state could reuse an earlier or production revision label (F5). Further findings F2 (acceptance evidence not tied to the candidate state), F3 (missing reproducibility settings), F4 (incomplete overwrite protection) and F6 (naming) remain open; the architecture overview records their owners.
- F1 correction: `validate_gain_execution` (action component) requires a gain receipt to supersede exactly the current artifacts that depend on the target, replaced one-for-one by kind and contributor set. Composite derivation and attempt reference validation both run it, so manually assembled candidates cannot bypass it. F5 correction: `ProjectState.known_revisions` and `ArtifactCheckpoint.known_revisions` support local revision-collision checks in preflight, derivation and attempt construction.
- Evidence: promoted audit reproductions plus regressions, and mutation checks of every new rule. Two initially undetected mutations (multiplicity, and baseline-only revision reuse at attempt construction) led to additional regressions. Metadata consistency only; no audio was processed. No fixture needed changing. Left uncommitted for review.

## 2026-10-21 — explicit processing specification (ADR-021)
- Implemented the owner-approved ProcessingSpec design, proposed as ADR-021. `domain.project.ProcessingSpec` records FLOAT64 calculation, the `10 ** (gain_db / 20)` gain rule on fixed inputs, recipe-sequential summation from a zero accumulator, level preservation (no clipping, normalization, limiting or automatic level correction) and 64-bit float WAV output. Every field is required, with no defaults.
- `ExecutionRequest`, `ProcessingPlan` and `ArtifactProcessingEvidence` gained a required `processing_spec`. Preflight carries the request's specification unchanged, and `validate_processed_candidate` requires each output's evidence to record a specification equal to the plan's as a complete value. Existing preflight outcomes and precedence are unchanged.
- This narrows audit finding F3 without closing it. Specification equality establishes recorded configuration consistency only: evidence is an adapter claim, and environment and code-version provenance and the adapter's subtype mapping are still required. No processing, audio or file operation was added. Left uncommitted for review.

## 2026-10-22 — in-memory gain and summation kernels (infrastructure.audio)
- Implemented `autonomous_mixing.infrastructure.audio` with `apply_absolute_gain` and `sum_stems`, the numerical rules of ADR-021's `ProcessingSpec` (FLOAT64, DB_AMPLITUDE, RECIPE_SEQUENTIAL, PRESERVE) on in-memory float64 `(frames, channels)` arrays. Gain uses `10 ** (gain_db / 20)` on the fixed input, requires a finite, strictly positive factor (subnormal allowed) and does not compound across calls. Summation adds each stem to a new zero accumulator in tuple order and checks finiteness after every addition, without reordering, compensation, normalization, clipping or limiting. Inputs are validated strictly (exact `np.ndarray`, native-endian float64, 1–2 channels, at least one frame, finite), never cast or mutated; outputs are newly allocated; the caller's NumPy error settings are restored. `gain_db` must be an exact built-in `int` or `float`.
- Evidence (independent review): 126 test cases in `tests/unit/test_audio_dsp.py` and the full suite of 1,218 tests passed; ruff lint and scoped formatting passed. The tests use synthetic numerical arrays; no perceptual or research-performance result follows from them, and no cross-platform bitwise identity is claimed.
- Limits: no WAV processing adapter, execution service or connected correction pipeline exists. Array immutability does not establish source-file protection. Audit findings F2 (acceptance evidence tied to the candidate state) and F4 (overwrite protection, workspace confinement) remain open; F3 is narrowed further but not closed (subtype mapping, actual evidence and environment provenance outstanding). Contributes to FR-08, FR-17, QR-01 and QR-02; no requirement status changed.
- Open: scalar normalization at the future adapter boundary is unresolved, because the domain's `require_finite` accepts `np.float64` and other `int`/`float` subclasses while the kernel accepts only exact built-in `int`/`float`. No test yet enforces the infrastructure package's own import boundary. Left uncommitted for review.

## 2026-10-22 — read-only WAV artifact verifier (infrastructure.audio.verification)
- Implemented `verify_wav_artifact(artifact, *, max_file_bytes, max_decoded_bytes) -> VerifiedAudio`. It checks one existing file against its `AudioArtifact` metadata.
  - It accepts only absolute local paths: relative, URI and UNC/network/device forms are rejected lexically.
  - It checks that the pathname and the opened handle are regular files, opens the file once and captures at most `max_file_bytes + 1` bytes in chunks of at most 1 MiB. The chunk size is an I/O buffer size, not a scientific threshold.
  - It compares the payload's SHA-256 with `content_hash` before decoding, then decodes the same captured bytes without reopening the path.
  - The container must be libsndfile WAV/WAVEX/RF64, detected from content rather than the file extension. Declared sample rate, channel count and frame count must match. The decoded-storage limit (frames × channels × 8 bytes) is checked before decoding.
  - It returns finite native float64 `(frames, channels)` samples, initially read-only, plus the detected container and subtype and the verified hash.
  - Expected failures raise `ArtifactVerificationError` (a `ValueError` subclass) with a `VerificationFailureCode`.
- Evidence (independent review): 87 test cases in `tests/unit/test_audio_verification.py` and 1,305 full-suite tests passed with no skips; ruff lint and scoped formatting passed. Tested on Windows with soundfile 0.14.0 (pinned in `uv.lock`) and libsndfile 1.2.2 (per the implementation report). The POSIX `IsADirectoryError` branch and real permission failures were not exercised on a real filesystem; permission failure was exercised only via a test double.
- Limits:
  - Only the captured payload's identity is established, not that the pathname is unchanged afterwards. A future processor must consume the verified samples or reverify what it reads.
  - There is no directory confinement, alias/symlink/reparse-point protection or race-free capture, and no guarantee on total process or decoder memory.
  - The read-only flag guards against accidental mutation only; it is not a security boundary.
  - No workspace allocator, WAV writer, processing adapter or connected execution pipeline exists. F2 and F4 remain open, and F3 is not closed.
  - No perceptual or research-performance result is established.
  - Contributes to QR-01, QR-02, QR-03, FR-02 and FR-17; no requirement status changed.
- Open: existing records use relative locations such as `outputs/{id}.wav`, which the verifier rejects. Their explicit resolution is unresolved and recorded under the overview's open questions. Left uncommitted for review.

## 2026-10-22 — application component packages and application/infrastructure import boundaries (ADR-022)
- Structural refactor only, implementing the owner-approved application/infrastructure component design (proposed as ADR-022). `autonomous_mixing.application` now consists of the components `shared`, `observation`, `decision`, `admission` and `execution`, each with an explicit interface. Every public definition moved unchanged (an AST comparison of all 43 moved definitions, including private helpers, against 218c3dc found no difference). The flat `application/admission.py` became the `application.admission` package at the same dotted path. The root and the legacy modules `contracts`, `results`, `execution_contracts`, `execution_preflight` and `ports` are re-export-only facades. `decision` received its own private copy of the unique-observation check (same check and messages) so that it does not depend on `observation`. `infrastructure.audio` (`dsp`, `verification`) was not changed.
- Tests: new `tests/unit/test_application_component_compatibility.py` (identity of all 36 public definitions at their canonical interface, owner module and every old path; root `__all__` unchanged including order; constructor, function and protocol signatures equal to the baseline) and `tests/unit/test_application_infrastructure_boundaries.py` (import checker with negative and positive fixtures for absolute, relative, from-package, aliased and look-alike forms; component matrix; facade contents; infrastructure source rules; acyclic component and module graphs; fresh-process imports in several orders and of every module alone). The new fixtures were written first and failed against a checker without the new rules (90 forbidden imports accepted, 4 permitted infrastructure imports rejected). Existing tests changed only in imports, two patch/inspection targets (`application.admission.admission`, `application.execution.preflight`) and file-discovery lists.
- Observed checks (Windows, existing uv environment): baseline at 218c3dc with a clean tree, 1,305 passed; after the refactor 1,498 passed, ruff lint passed, scoped formatting check passed (48 files), CLI help and `uv lock --check` passed. A separate signature comparison with the recorded baseline found 0 differences across 120 entries. The fresh-process import checks roughly double the suite's wall time (about 15 s).
- Limits: no behavior, requirement status or research claim changed. No location resolution, workspace allocation, WAV writing, adapter or orchestration was added. F2 and F4 remain open, and F3 is not closed. The infrastructure rules govern source imports only; they establish no runtime isolation, filesystem protection or adapter. The old modules no longer re-export names they merely imported (domain records, `dataclass`, the `results.T` type variable). Left uncommitted for review.
- Open: the allowed imports and filesystem dependencies of a future processing adapter, and when the compatibility facades can be removed.

## 2026-11-02 — read-only artifact-location resolution (ADR-023)
- Implemented the owner-approved design "Read-only artifact-location resolution", proposed as ADR-023. New component `infrastructure.filesystem` with these exports:
  - `ResolvedArtifactLocation`;
  - `LocationResolutionFailureCode`, with seven codes;
  - `LocationResolutionError`;
  - `resolve_artifact_location(artifact, *, root)`.
- Resolver behavior:
  - It resolves an artifact location against an explicit native absolute root without opening content. There is no current-directory fallback, URI decoding, or environment or tilde expansion.
  - It applies lexical rejections, including any `..` component.
  - It resolves the root and the candidate strictly, then checks canonical component-wise containment. Root equality is rejected; inside-root links and junctions are allowed and outside targets are rejected.
  - Failures are typed with a fixed precedence, and only narrow exceptions are mapped.
- The original `AudioArtifact` object and all its metadata are kept unchanged. A relative location stays relative.
- `verify_resolved_wav_artifact`:
  - It re-resolves the original artifact against the recorded `configured_root` and raises `STALE_RESOLUTION` on any exact-string difference before opening content.
  - It then uses the capture/hash/decode pipeline it shares with `verify_wav_artifact`.
  - `verify_wav_artifact` is unchanged, and the audio facade now has seven exports.
  - The earlier open question on relative artifact locations is answered in the overview.
- Implementation choices observed in code:
  - The URI pattern is reused from the verifier, so a POSIX relative name like `take:1.wav` is rejected.
  - Two leading separators are treated as UNC on all platforms.
  - Containment compares components exactly, so a Windows case-only mismatch would fail closed as `OUTSIDE_ROOT`. No dedicated test covers this.
  - The stale comparison uses exact strings.
  - The Windows `..` check matches exact components, with containment as backstop.
  - Only an exact `RuntimeError` maps to `RESOLUTION_LOOP`.
- Evidence:
  - Implementer rerun: full suite 1,870 passed, 1 skipped (POSIX-only permission test in `tests/unit/test_artifact_locations.py`, "POSIX non-root only").
  - Independent review: 1,866 passed, 5 skipped; four additional junction tests could not run because of sandbox permissions in the review environment. Ruff lint, scoped formatting and CLI help passed.
  - Implementer real-link evidence on Windows 11 (Developer Mode): symlinks and junctions inside→inside allowed, inside→outside rejected, root aliases, real symlink and junction cycles.
  - Permission failures, the `ELOOP` branch and unexpected-error propagation were exercised only through test doubles, not as real-platform evidence.
- Observation: an import-order test failed once in the implementer's baseline full run, before any change, and was not reproduced in later runs. Its cause has not been diagnosed, and no fix is claimed.
- Limits:
  - There is no race-free confinement, write or source protection, hard-link uniqueness, exclusive access, workspace allocation, WAV writing or connected execution pipeline.
  - F2 and F4 remain open, and F3 is unchanged.
  - No requirement is completed and no requirement status changed.
  - No perceptual or research-performance result follows.
  - Left uncommitted for review.
- Open: who supplies and records the root in a run; workspace allocation; alias and race handling; whether the POSIX name restrictions are intended.

## 2026-11-02 — clarification of the resolver documentation (owner decisions)
- This entry clarifies the preceding 2026-11-02 resolver entry and does not change it.
- Windows alternate data streams, and any colon outside the drive prefix, are rejected by intent.
- The URI-style, Windows-drive and two-leading-separator (UNC) exclusions are intentional supported-input restrictions on all platforms. Valid POSIX names such as `take:1.wav`, `a:b.wav` or `//`-prefixed paths are outside the supported input form. The earlier open item asking whether these POSIX restrictions are intended is closed.
- Containment is exact path-component comparison after strict canonical resolution. The earlier remark that a Windows case-only mismatch would fail as `OUTSIDE_ROOT` is superseded. The independent reviewer's native Windows casing probe passed: a differently cased but equivalent path resolved within the root. This was a one-off review probe; the suite has no dedicated casing test.
- Unchanged:
  - the exact-string `STALE_RESOLUTION` rule;
  - all verification figures and their attributions;
  - ADR-023 remains Proposed;
  - there is still no race-free confinement, write or source protection, workspace allocation, WAV writing or connected pipeline;
  - F2 and F4 remain open.

## 2026-11-02 — fresh output workspace allocation (ADR-024)
- Implemented the owner-approved design "Fresh output workspace allocation", proposed as ADR-024.
- New component `infrastructure.workspaces` with six exports: `WorkspaceRoot`, `WorkspaceAllocationRequest`, `AllocatedWorkspace`, `WorkspaceAllocationFailureCode` (nine codes), `WorkspaceAllocationError(code, message, *, created_directory=None)` and `allocate_workspace(request)`.
- Imports: `allocation` imports only the standard library and the `domain.project`, `application.execution` and `filesystem` public interfaces. This is the first narrowly scoped infrastructure→application dependency. The domain and application still never import infrastructure, and importing `audio` or `filesystem` loads no workspaces or application modules.
- Allocation steps:
  - It requires exact root-key agreement and an explicit existing root in the resolver's native absolute syntax.
  - It uses `directory_token` unchanged as the sole child name, rechecks its ASCII pattern and, on Windows, rejects reserved device names.
  - It requires the protected locations to cover the union of current audio members and fixed track-gain input baselines with exactly equal metadata. Caller-supplied history entries get the same checks but are not proven exhaustive.
  - It re-resolves every protected entry freshly, requires exact record equality and a regular-file stat, and opens no content.
  - It rejects component overlap with the planned child (`PurePath.is_relative_to`, case-insensitive on Windows; per-directory case sensitivity is not handled).
- Creation and failures:
  - It creates the child with one exclusive `mkdir(parents=False, exist_ok=False)`. Any existing child is a `COLLISION` and is left untouched, with no reuse, suffix or retry.
  - After creation it re-resolves the root and child, rechecks the root directory, and requires an exact direct child that is a real directory and empty. Expected failures give `WORKSPACE_CHANGED` with `created_directory`, and the directory is retained with no cleanup.
  - Narrow mappings: a root `ELOOP` before `mkdir` gives `INVALID_ROOT`; `ELOOP` in root inspection, `lstat` or the listing after creation gives `WORKSPACE_CHANGED`; unrelated errors propagate.
  - The post-creation root directory recheck was added during the review-driven `ELOOP` correction.
  - Resolver, verifier and DSP behavior and exports are unchanged (`filesystem` 4, `audio` 7).
- Evidence:
  - Coordinator-reported full run: 2,289 passed, 2 platform skips (`tests/unit/test_artifact_locations.py` "POSIX non-root only"; `tests/unit/test_workspace_allocation.py` "POSIX hosts impose no Windows device-name restriction").
  - Independent correction review: 645 passed, 5 skips; four of these are additional junction skips caused by sandbox restrictions in the review environment. Independent scoped lint and formatting passed.
  - No fresh lockfile check: uv was unavailable in the environment; `pyproject.toml` and `uv.lock` are unchanged.
  - Implementer-reported native evidence (Windows 11, Developer Mode): real collisions with file, directory, symlink, dangling-link and junction children; real root aliases via symlink and junction; real post-`mkdir` replacements; concurrent same-assignment allocation over 10 rounds, each giving one success and one `COLLISION`.
  - Permission errors, reparse attributes, `ELOOP` and unexpected-error propagation were exercised only via injected doubles, not as real-platform evidence.
- Limits:
  - There is no complete historical inventory guarantee, durable ownership, execution lock, race-free protection or confinement, or cleanup and recovery.
  - There is no WAV writing and no connected execution pipeline, and the result is not a security capability.
  - F4 remains open (reduced, not closed), and F2 is unaffected.
  - No requirement is completed and no requirement status changed.
  - No perceptual or research-performance result follows.
  - Left uncommitted for review.
- Open: a history and retention registry; durable ownership and cleanup; execution locks; the writer's no-overwrite protocol; per-directory case sensitivity in overlap checks.

## 2026-11-03 — exclusive WAV output and workspace revalidation (ADR-025)
- Implemented the owner-approved design "Exclusive WAV output", proposed as ADR-025. Documented after independent review; the code is uncommitted.
- New public workspace revalidation `validate_allocated_workspace(workspace: AllocatedWorkspace) -> None` in `workspaces.validation`. It is read-only, does not allocate and does not require an empty directory. The `workspaces` facade now has seven exports; `allocation` is unchanged.
- New component `infrastructure.wav_output` with four exports: `WrittenWav` (frozen, 11 fields), `WavOutputFailureCode` (8 members), `WavOutputError(code, message, *, created_file=None)` and `write_wav_output(workspace, filename, samples, *, render_spec, processing_spec, max_file_bytes, max_decoded_bytes) -> WrittenWav`.
- Imports: `writing` imports only the standard library, NumPy, soundfile, `domain.project`, `domain.observation` and the `workspaces` interface. `audio` and `filesystem` imports load neither `workspaces` nor `wav_output`. Nothing imports `wav_output` yet.
- Protocol:
  - In-memory WAV/DOUBLE encoding, validated before the destination is created, with exact sample preservation including signed zeros, subnormals and finite overrange values.
  - Explicit size limits; bounded chunked I/O of at most 1 MiB per read or write, so huge limits are supported.
  - Revalidation immediately before creation, then one `open(path, "x+b", buffering=0, opener=...)`; soundfile never opens the destination path.
  - On Windows the opener calls `CreateFileW` with `CREATE_NEW | FILE_FLAG_OPEN_REPARSE_POINT` through ctypes, then `msvcrt.open_osfhandle`. Reason: plain `x+b` followed a dangling file symbolic link and created its target outside the workspace. This was found during implementation, and the owner approved this opener (Option A). POSIX uses the default exclusive opener.
  - Readback and SHA-256 through the created handle (captured bytes, not the encoding buffer), covering metadata, subtype and exact samples; a second revalidation; a successful close before success is reported.
  - Collision rejection for every existing form (file, directory, symbolic links, dangling links, junctions), with no overwrite or truncation.
  - Failures after creation keep the file and set `created_file`; there is no deletion, retry or cleanup.
  - Close-error priority: the primary failure is kept when an ordinary secondary close error occurs, with a note on `WavOutputError` only; foreign exceptions propagate unchanged. Correction: if descriptor attachment fails and the raw-handle close also fails, the original attachment exception is re-raised as the same object.
- Implementation deviations from the written design:
  - Sample rates above `2**31 - 1` are rejected with plain `ValueError` before encoding, because soundfile raises `OverflowError`.
  - `WrittenWav` also cross-checks sample rate and channel count against `render_spec`.
- Evidence:
  - Coordinator-reported full run: 2,778 passed, 3 platform skips: `tests/unit/test_artifact_locations.py:730` ("POSIX non-root only"), `tests/unit/test_wav_output.py:1181` ("POSIX keeps the default O_CREAT | O_EXCL opener") and `tests/unit/test_workspace_allocation.py:610` (POSIX device-name test).
  - Independent correction review: all 12 new regressions passed.
  - Independent writer-focused run: 275 passed, 4 skips. Three were junction skips caused by sandbox restrictions; one was POSIX-only.
  - Independent scoped lint and formatting passed.
  - No fresh lockfile check: uv was unavailable; `pyproject.toml` and `uv.lock` are unchanged.
  - Implementer-reported real Windows 11 evidence, not rerun by the coordinator except through the full suite: collision forms including dangling links with the target not created; same-name concurrency over 10 rounds, each giving one success and one `COLLISION`; the bit-exact roundtrip; a real protected-file deletion giving `WORKSPACE_CHANGED` with the file retained.
  - Implementer-reported injected-only evidence (controlled doubles, not real-platform behaviour): short, zero and invalid writes; flush, seek, read and close failures; corrupted readback; attachment and close failures.
  - Documentation probe (2026-11-03, one-off, not regression coverage), using the repository's existing environment (soundfile 0.14.0, libsndfile 1.2.2): two in-memory encodings of identical samples about one second apart contained a PEAK chunk and differed in bytes. Python 3.11 maps Win32 error 1921 to a plain `OSError` with errno 22, not `ELOOP`.
- Limits:
  - Exact sample preservation does not imply reproducible WAV bytes or hashes; libsndfile 1.2.2 writes a PEAK chunk with a timestamp, so bytes and hashes can differ between encodings of identical samples.
  - An unexpected descriptor-attachment failure may leave an empty file without `created_file`.
  - Ordinary secondary close errors preserve the primary failure, but a secondary `KeyboardInterrupt` or `SystemExit` may supersede it.
  - Parent paths are not pinned. There is no race-free confinement, fsync durability, atomic publication, two-output transaction, lock or complete history guarantee.
  - No connected DSP processing adapter or execution pipeline exists.
  - F2 is unaffected and F4 stays open. Subtype mapping is implemented at this writer boundary only.
  - No requirement is completed and no requirement status changed. No perceptual or research-performance result follows.
  - Left uncommitted for review.
- Open: hash reproducibility policy; Windows error 1921 propagating as unexpected; the empty-file-on-attachment-failure limit; whether the Windows opener should deny concurrent writers.

## 2026-11-03 — M0 CLI report destination guard (defect correction)
- Bounded defect correction, no ADR. Before it, `autonomous-mixing analyze` wrote the report to `--output` without comparing it with the inputs, so a destination naming an input stem replaced that source file with JSON. Documented after independent review; the change to `src/autonomous_mixing/cli.py` and `tests/test_cli.py` is uncommitted.
- Behaviour: after successful analysis and input validation, the CLI compares filesystem identity (`os.stat`, following symbolic links, plus `os.path.samestat`; no string comparison) of the destination with each input, before creating the parent directory and again after, immediately before writing. Direct paths and relative/absolute, symbolic-link and hard-link aliases are covered where the platform supports them. Rejection: exit code 2, one-line `error: …` on stderr, no traceback, source bytes preserved. A nonexistent destination is allowed; any other `OSError` while inspecting an existing destination or an input rejects without writing. New reports and replacement of unrelated existing reports still work; arguments, report schema and extension handling are unchanged.
- Evidence (by source):
  - Implementer full run: 2,791 passed, 3 skips.
  - Independent full run: 2,777 passed, 17 platform/sandbox skips.
  - Both original source-overwrite audit probes now pass.
  - An independent controlled second-check probe passed; it is not native POSIX evidence or permanent regression coverage.
  - Lint, formatting and CLI help passed.
  - No fresh lockfile check: uv was unavailable; dependency files are unchanged.
  - Identity was exercised on Windows 11 NTFS only.
- Correction to the briefed behaviour change for an output whose parent component is an existing file: it was stated as now giving exit 2 with `error: cannot verify …` instead of a traceback. That follows from the code only where `os.stat` raises a non-`FileNotFoundError` error (expected on POSIX, not exercised natively). A documentation probe on Windows 11 (2026-11-03, one-off, not regression coverage) observed `FileNotFoundError` from `os.stat`, so the first check allowed the path and parent creation raised an uncaught `FileExistsError` traceback, as before; no input was written.
- Limits: not a lock and not race-free; parent directories created before a second-check rejection remain. F4 (future file adapter) is unaffected. No requirement status changed; QR-01 remains untested as recorded.
- Open: the Windows file-as-parent traceback; native POSIX behaviour of the guard.

## 2026-11-03 — M0 CLI report-parent creation failures (follow-up correction)
- Supersedes the correction bullet and the open item "the Windows file-as-parent traceback" in the earlier 2026-11-03 entry "M0 CLI report destination guard (defect correction)": that traceback is resolved. The earlier entry is left unchanged. Documented after independent review; the follow-up change to `src/autonomous_mixing/cli.py` and `tests/test_cli.py` is uncommitted.
- Behaviour: only `path.parent.mkdir(parents=True, exist_ok=True)` is wrapped. On `FileExistsError`, `NotADirectoryError`, `PermissionError` or `FileNotFoundError` the CLI prints `error: cannot create report directory <parent>: <exc>` on stderr and returns 2. No report is written and no `Wrote …` line is printed; inputs and blocking files are unchanged. The order stays check, parent creation, check, write, and both identity checks remain.
- Evidence (by source):
  - Implementer full run: 2,801 passed, 3 platform skips.
  - Independent focused review: 25 CLI tests passed; 2 hard-link skips were caused by sandbox permissions.
  - Independent scoped lint, formatting and CLI help passed.
  - No independent full-suite rerun for this small follow-up.
  - Native Windows file-as-parent cases passed; there was no native POSIX run. On POSIX the identity check is expected to reject these cases first with `error: cannot verify …`; this is untested.
  - uv was unavailable; dependency files are unchanged.
  - No real `PermissionError` from parent creation was produced; that type is covered only by an injected test.
- Limits: unexpected errors from parent creation still propagate, and report-writing errors are not covered by this change; not every CLI filesystem error is handled. Parent directories created before a failure are not cleaned up. No locking or race-free guarantee is added. No requirement status changed.
- Open: native POSIX behaviour of the guard and of these cases.
