# R1 production directional measurements: Claude implementation handoff

> **For agentic workers:** Use superpowers:executing-plans, or superpowers:subagent-driven-development only when separately delegated. Implement tests first, one reviewable task at a time. Do not commit automatically.

**Goal:** Integrate descriptive directional measurements and reproducible support preparation using the reviewed P1 kernels, without inventing calibration or producing diagnoses.

**Architecture:** Immutable core records describe preparation, support and metric identity. The DSP adapter owns verified file reads, numerical arrays and evidence persistence. A measurement router preserves the existing per-job analyzer identities. No coordinator or provider integration in this increment.

**Tech stack:** Existing Python records, NumPy kernels, shared WAV verification and current architecture-test conventions. No new dependency by default.

**Spec:** Read `docs/superpowers/specs/2026-11-20-p1-diagnosis-method.md` and the R1 section of `docs/superpowers/plans/2026-11-20-diagnosis-and-decision-adapter.md` before coding. The owner accepted R0's specification; numerical calibration has not run.

## Global constraints

- Preserve all existing uncommitted files. Inspect repository status and applicable instructions first. Stop for a genuine design conflict; do not silently weaken the accepted method.
- Mono 44,100 Hz PCM_16 only for P1, with exact recovered int16 counts. No conversion, normalization, resampling, multi-masker sum or implicit alignment correction.
- Run the reviewed front end from call 0. Keep its numerical formula, order, history and band mapping unchanged.
- No scientific gate values are approved. Do not select deadbands, activity/noise thresholds, minimum evidence counts, candidate fractions or persistence durations.
- No ProblemCandidate, DiagnosisSource, AI provider, decision-file substitute, coordinator wiring, CLI enablement, install or live FL Studio call.
- Do not read hidden clean/manual references or evaluation-only roots. Original projects, exports and existing captures are read-only.
- Architectural exceptions need explicit review; no adapter importing a core stage or another adapter component outside the existing permitted shared-reader edges.

## Important scope split

R0 distinguishes geometric support U from calibrated initial qualified support S0. R1 must not substitute one for the other.

Implement descriptive geometry and raw model evidence now. Represent qualification as unavailable/not established, not as all-pass, empty-success, a zero-valued measurement or a policy with convenient default thresholds. Only final, explicitly qualified S0 may back the qualified metric IDs in R0. Uncalibrated preparation may produce a distinct descriptive plan for U, but must never label it a qualified plan.

If exposing descriptive scalar summaries, allocate distinct versioned raw/descriptive metric IDs and define their denominator explicitly. Never place an all-geometric-cell summary under a metric whose contract requires S0. Include exact-silence and quiet-floor metadata; positive raw margins involving a silent target are not a masking diagnosis. A required qualified summary returns Unavailable with an explicit uncalibrated-policy reason.

## Review focus

1. Opposite directions accidentally deduplicate: pin full ordered scope and source identity in tests.
2. Geometric support gets advertised as calibrated support: reject qualified requests until calibration exists.
3. Changing activity or sample geometry improves a denominator: persist exact support and refuse mismatches.
4. Mixed jobs relabel level evidence or validate incomplete subrequests: preserve identities and validate the final merged response.
5. A saved manifest or evidence file is partial, altered or resolves outside runtime roots: reject it before returning usable observations.

## Task 1: Preparation and measurement records

**Files:** create `src/autonomous_mixing/core/records/masking_measurement.py`; tests in `tests/unit/test_masking_measurement_contract.py`. Add inventory entries under existing architecture conventions.

**Interfaces:** define `MaskingPreparationRequest`, `MaskingPreparedPlan` and `MaskingMeasurementPlan`, matching R0's lifecycle. Use distinct types or an explicit validated status to prevent a descriptive plan being consumed as qualified. Core records contain immutable Python data, no arrays, paths requiring I/O, SDK types or storage behavior.

- [ ] Write failing tests for source ordering, exact profile, sample geometry, alignment provenance, complete-window regions, band selection and support identity.
- [ ] Pin that `(A, B)` and `(B, A)` are different scopes and ordered source requirements.
- [ ] Pin that an unavailable qualification policy cannot create a qualified plan or populate qualified observations.
- [ ] Implement the smallest records and shared checks needed by the accepted method.
- [ ] Run focused tests with warnings as errors; update exact public-contract/inventory pins explicitly.

Record original baseline hashes as support provenance. Do not require later candidate bytes to equal baseline bytes; later observations bind their actual current artifacts and the same frozen support geometry. Different support cells with equal counts are still incompatible.

## Task 2: Deterministic support preparation and persistence

**Files:** create `src/autonomous_mixing/adapters/dsp_analysis/masking_preparation.py`; tests in `tests/unit/test_masking_preparation.py`. Keep file verification/evidence persistence separate from pure geometry calculations if the module becomes large.

**Interfaces:** preparation consumes the validated request and configured runtime roots/read limits; produces a persisted immutable descriptive prepared plan, or an explicit failure. A calibrated S0 is not produced in this increment.

- [ ] Write red tests for exact eligible calls, full-window interval containment, integral sample boundaries, zero calls, unsupported bands and equal-count/different-support identity.
- [ ] Use the existing verified WAV boundary; unknown alignment provenance and incompatible inputs fail explicitly.
- [ ] Persist a versioned canonical manifest with source/policy/profile identity, exact support cells, evidence references and digest. Verify it on reload; do not silently recreate altered/missing support.
- [ ] Test failed/partial persistence, tampering, unsupported schema and missing evidence. Do not return success before the complete evidence needed by the result is recorded.
- [ ] Record resource limits, cancellation/time observations and preparation timing. There is no analysis-call budget to charge, and R1 does not modify the coordinator's budget model.

Preparation occurs before a future final RunRequest is built. R1 exposes the operation; R5 will compose it. Do not mutate an existing immutable RunRequest or its AnalysisSpec after analysis.

## Task 3: File-backed directional analyzer

**Files:** create `src/autonomous_mixing/adapters/dsp_analysis/directional_analysis.py`; tests in `tests/unit/test_directional_analysis.py`. Use the existing fixtures under `tests/fixtures/mpeg_psy_model2_dist10/` unchanged.

**Interfaces:** `DirectionalMaskingAnalyzer.analyze(AnalysisRequest)` implements the existing AudioAnalyzer port for its declared descriptive jobs. Its configured plan registry accepts only exact immutable prepared-plan/specification identities.

- [ ] Write red tests that replay captured T/E values and directional margin arithmetic independently of production aggregation.
- [ ] Test source reversal, both directions in one request, current-artifact binding, sample-rate/channel/subtype errors and geometry/alignment failures.
- [ ] Compose `read_dist10_pcm16`, `dist10_clip_to_bands` and `directional_masking_margin_db`. No duplicate numerical kernel or import from research scripts.
- [ ] Derive the quiet band vector Q through the pinned qthr table and existing band mapping; do not assume ClipBandAnalysis exposes partition attribution.
- [ ] Retain all call metadata, exact silence and all 21 bands as descriptive evidence. Frequency-specific diagnosis remains limited to bands 1-20; no diagnosis is built here.
- [ ] Persist raw grids, exact support, validity/silence/quiet flags and provenance in a documented bounded schema. No NaN/Inf masquerading as JSON numbers. Core observations remain scalar or Unavailable.
- [ ] Test startup and truncated boundaries, short-path calls, quiet floor, near-zero margins, exact silence and low-energy values. Raw evidence must not become a sufficiency claim.
- [ ] Validate every successful response against the original analysis request. Unknown metrics and malformed jobs fail explicitly; never return a partial successful batch.

Numerical comparison limits must match each test's evidence. Do not use the pilot's 0.005552 dB maximum as a general tolerance. Capture-input replay and full-chain residual tests are different claims and should be labelled separately.

## Task 4: Mixed measurement router

**Files:** create `src/autonomous_mixing/adapters/dsp_analysis/analysis_router.py`; tests in `tests/unit/test_analysis_router.py`.

**Interface:** `AnalysisRouter.analyze(AnalysisRequest)` dispatches supported jobs to the existing level analyzer and the new directional analyzer.

- [ ] Write failing tests for mixed jobs, all-level jobs, all-directional jobs, duplicate/unsupported definitions and one failed sub-batch.
- [ ] Preserve each job's analyzer identity. FileAudioAnalyzer observations keep `autonomous_mixing.file_level_analyzer@1`; do not relabel them as P1 or as a composite algorithm.
- [ ] Build subrequests with the same AnalysisSpec.ref, only their selected metric_ids/definitions, and the original exact current-member root bindings.
- [ ] Validate the merged response against the original request, including exact coverage and unique observation IDs.
- [ ] Prove level results and native stereo handling are unchanged. The router does not make stereo valid for P1.

## Task 5: Verification and review package

- [ ] Run the relevant new/affected tests with `uv run --locked pytest ... -W error`, plus the fixture verifier using the repository's documented command.
- [ ] Run architecture checks, the full suite, `uv run --locked ruff check .`, scoped/full required format checks, `uv lock --check` and `git diff --check`.
- [ ] Use focused mutation probes for direction reversal, startup/history omission, changing support, treating uncalibrated policy as qualified, relabeling analyzer identities and missing evidence validation. Restore every file byte-identically and verify restoration.
- [ ] Update the accepted specification only with implementation evidence or a clearly marked proposed deviation; do not invent calibration results or quietly change its scientific choices.
- [ ] Append the research log and update architecture inventories. Propose an ADR only for a genuine new architecture decision, without editing accepted ADRs.
- [ ] Return changed paths, exact check results, capture comparisons, remaining limitations and design deviations. Leave everything uncommitted for Codex review.

**Completion boundary:** real descriptive measurements and reproducible preparation are available through the existing analysis contract. Qualified diagnosis, autonomous decision-making, provider calls and live DAW execution remain unavailable.
