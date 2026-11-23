# Experimental fixed-support diagnosis implementation plan

> **For agentic workers:** Use superpowers:executing-plans or the equivalent tests-first workflow to implement task by task. Leave everything uncommitted for Codex review; do not auto-commit.

**Goal:** Turn the existing experimental fixed-support measurements into deterministic, explicitly uncalibrated problem hypotheses or honest incomplete coverage.

**Architecture:** Extend the existing masking diagnosis records, pure rule and configured diagnosis source. Preserve the legacy R2A readiness route. No new adapter, provider, protocol, coordinator stage or general diagnosis framework.

**Tech stack:** Existing Python immutable records, scalar observations, pytest and architecture checks; no dependencies.

**Spec:** Existing diagnosis/decision plan, P1 method spec, p1-experimental-policy-pilot.md (including the owner current-values decision), and 2026-11-23-experimental-fixed-support.md. This handoff specifies the experimental continuation, not scientific qualification.

## Authorization and boundaries

The owner requested delegating the next implementation to Claude. Implement this slice tests-first, leave uncommitted for review. First read AGENTS.md/CLAUDE.md and the required project documents; inspect HEAD, status and existing code. Preserve unrelated uncommitted work. Do not assume the measurement increment has been committed: report the starting state and retain it.

Current approved development configuration is explicit: energy reserve 40 dB, quiet elevation 0.02 dB, margin deadband 0.02 dB, minimum 100 cells, minimum robust fraction 0.25, minimum persistence 4 calls. These already belong to ExperimentalMaskingPolicy. Do not add defaults, duplicate policy values, tune them, change support or infer calibration. Tests may construct other explicitly labelled boundary configurations.

No provider/network calls, FL operations, installs, real-music analysis, dataset generation, scientific-study reruns, acceptance-policy changes or hidden-reference access. No R5 composition or CLI activation. A hypothesis never specifies a gain cut or proves audible masking/better mixing.

## Existing interfaces to inspect

- core/records/masking_measurement.py: ExperimentalMaskingPolicy, ExperimentalSupport, MaskingMeasurementPlan, bound_specification.
- adapters/dsp_analysis/directional_analysis.py: p1.experimental support_cells, robust_exceedance_cells, longest_robust_run_seconds; all @1. The separate positive_margin_fraction is NOT robust fraction.
- core/records/masking_diagnosis.py: MaskingDiagnosisJob/Policy, presently legacy readiness only.
- core/diagnosis/masking.py: diagnose_masking and exact current source matching.
- drivers/diagnosis_runtime.py: ConfiguredDiagnosisSource and response validation/identity tracking.
- core/records/diagnosis.py: DiagnosisCoverage, ProblemCandidate, DiagnosticEvidence, validate_diagnosis_references.
- tests/unit/masking_diagnosis_support.py and existing diagnosis tests; experimental measurement fixtures in test_experimental_masking.py.

## Review focus

1. A current scalar with the right metric but another artifact/specification must not become evidence.
2. Robust fraction must use robust count divided by the frozen count, never ordinary positive count/fraction.
3. Too little support/unavailable current data must not become assessed absence.
4. Experimental method identity must bind the complete prepared plan, including policy/cells, without implying calibration.
5. Multiple jobs/directions and repeated calls must not cross-link candidates, coverage or identities.

## Task 1 — Explicit experimental diagnosis bindings

**Modify:** core/records/masking_diagnosis.py; architecture public inventories.
**Test:** extend test_masking_diagnosis_contract.py, or a focused test_experimental_masking_diagnosis.py if clearer.

Add frozen ExperimentalMaskingDiagnosisJob and ExperimentalMaskingDiagnosisPolicy alongside the legacy records. A job holds its full ExperimentalSupport and exactly three MeasurementDefinition records:

- p1.experimental.support_cells@1, cells;
- p1.experimental.robust_exceedance_cells@1, cells;
- p1.experimental.longest_robust_run_seconds@1, s.

The policy holds method, measurement_plan and jobs. It obtains screening thresholds from measurement_plan.prepared.experimental_policy; no independent threshold copy is accepted. Require that policy/extension exist. Require exact full support membership, unique pair/time jobs, shared exact ordered source requirements, scope, alignment, analyzer and specification consistent with the reviewed plan. Reuse existing record validation where possible; do not import adapters into core.

Pin the method to bound_specification("p1.experimental.diagnosis", "1", measurement_plan.prepared.digest). This names an operational diagnosis method separately from the screening method; the prepared digest includes the policy and frozen cells. Do not accept a user-labelled calibrated/qualified method or a method bound to another plan.

- [ ] Write failing constructor tests: valid binding, no experimental extension, wrong policy/support/source/order/geometry/spec/analyzer/unit/metric, duplicate job/metric, method digest mismatch.
- [ ] Implement the minimal records and shared validation, preserving old constructors and legacy tests.
- [ ] Run contract and architecture tests; pin new public names only where required.

## Task 2 — Deterministic rule and truthful evidence

**Modify:** core/diagnosis/masking.py.
**Interface:** diagnose_experimental_masking(request: DiagnosisRequest, *, policy: ExperimentalMaskingDiagnosisPolicy, identities: IdentitySource) -> DiagnosisReport.
**Test:** focused experimental diagnosis tests, with labelled scalar fixtures (no detector/real audio claim).

Resolve each ordered source uniquely among current state artifacts using all existing SourceRequirement fields. No newest-revision/first-match heuristic. Require exactly one observation per configured selector; its full analyzer/spec/scope/unit and ordered artifact IDs must match. Duplicate observation IDs and stale/project/revision/source errors must be handled through existing request/reference validators. Do not call DSP or load evidence/manifests/files in the pure rule.

For well-bound scalars require:
- support count integral and exactly equal to len(job.support.cells);
- robust count integral in [0, support count];
- run finite/nonnegative and consistent with an integer call count on the existing 576/44100 grid (reconstruct steps and require equality to steps*576/44100, rather than adding a new tolerance);
- run steps <= robust count and <= longest possible consecutive same-band run in the frozen cells.

Missing, unavailable, duplicate/ambiguous, wrong-bound or internally inconsistent measurement evidence yields INCOMPLETE for that job and no candidate, with a specific explanation. Invalid configuration, out-of-request scope and broken identity sources remain programming/configuration errors. Do not silently convert all exceptions to incomplete.

If fixed count < policy.minimum_cells, return INCOMPLETE with no candidate, even if fabricated finite fixtures claim large exceedance. Otherwise, with all three measurements usable, return ASSESSED under the experimental method. This means only this experimental rule ran, not scientific validation/all-clear.

Emit one ProblemCandidate for that job iff BOTH are true (inclusive):

    robust_count / frozen_count >= policy.minimum_robust_fraction
    longest_run_seconds >= policy.minimum_consecutive_calls * 576 / 44100

Do not use p1.experimental.positive_margin_fraction; it counts M>0, not M>deadband. Do not reapply the deadband to aggregate scalar values. The analyzer already counts strict M > configured deadband.

Use one MASKING coverage row per configured ordered job scope, so mixed usable/incomplete jobs are represented honestly. P2 frequency buildup and P3 phase interaction stay UNAVAILABLE over the request scope. Each candidate has exactly that job's three observation IDs, current project/revision, ordered scope and its coverage/method.

DiagnosticEvidence must say experimental dist10 exceedance hypothesis; representation UNCALIBRATED. With usable operational evidence, insufficient_evidence=False refers only to these declared engineering predicates, never scientific sufficiency. Explicit limitations: uncalibrated thresholds/no perceptual validation, declared alignment, theoretical noise reference, overlapping-window grid duration, no automatic correction implication. Include reproducible method/prepared/support/policy identities and actual counts/ratio/run/thresholds in public descriptions, without paths or withheld reference data. No confidence probability/severity inferred from the score.

- [ ] Write failing positive case at n=100, r=25, run=4*576/44100; test each boundary below separately.
- [ ] Test zero/small support -> INCOMPLETE; ordinary-positive fraction high but robust count low -> assessed no candidate.
- [ ] Test every binding failure, unavailable metric, fractional/negative/excess counts, support-count mismatch and inconsistent runs; all no candidate with correct status.
- [ ] Test opposite directions, multiple jobs with mixed availability, unchanged current members from older revisions, ambiguous same-subject artifacts, request-scope escape and duplicate/reused IDs.
- [ ] Implement the rule with shared matching/validation only where useful; preserve the legacy diagnose_masking readiness output semantics.
- [ ] Validate reports with the existing diagnosis-response/reference validators.

## Task 3 — Extend standalone source and integrate real measurements in tests

**Modify:** drivers/diagnosis_runtime.py; existing diagnosis driver tests.

Allow ConfiguredDiagnosisSource's explicit policy to be either legacy MaskingDiagnosisPolicy or ExperimentalMaskingDiagnosisPolicy. Dispatch to the corresponding pure rule. Do not select by metric-name guessing or load policies automatically. Track both coverage IDs and candidate IDs across calls; prevent reuse and collisions within the established identifier rules. Preserve response validation and existing PortResult behavior.

- [ ] Write failing dispatch/repeated-ID tests. Legacy source remains readiness-only, no candidates.
- [ ] Add one integration test using real preparation + DirectionalMaskingAnalyzer on disposable numerical WAVs, then this source; no fake production observations. A test-only explicit policy/support with enough usable cells may exercise the positive path; label it engineering integration, not validation data. Also exercise small/unavailable support. No threshold tuning to music.
- [ ] Prove no file/provider/evaluation/DAW imports in core; update only necessary inventories/isolation tests. Keep the router, DSP, coordinator, CLI and provider untouched unless a real interface defect is demonstrated and reported.

## Task 4 — Verification and documentation

- [ ] Run focused tests with -W error: experimental diagnosis + legacy diagnosis records/rule/runtime + experimental measurement + architecture.
- [ ] Run the full suite through uv run --locked --extra agent pytest -q; Ruff check/format, CLI help, uv lock --check and git diff --check.
- [ ] Faithful mutation probes: replace robust count by positive count; change >= fraction to >; change >= persistence to >; remove minimum support; skip current source identity; skip specification/analyzer binding; accept another support/policy; treat Unavailable as zero; leak a candidate from one direction into another. Every probe must be caught by a permanent behavioral test and source restored byte-identically. Explain equivalent/unreachable probes honestly instead of padding counts.
- [ ] Update the current diagnosis plan/spec implementation notes and overview; append research log. Correct stale status wording only for actual completion. Do not rewrite accepted ADRs or historical numerical results. No new ADR merely for extending this existing responsibility; document an actual boundary change separately if one arises.
- [ ] Report starting HEAD/tree, changed files, red/green evidence, checks/probes, limitations and next step. Leave all changes uncommitted for Codex review.

## Completion and next slice

Done means genuine experimental observations yield reproducible supported hypotheses or no candidate, while legacy readiness stays unchanged and uncalibrated claims remain explicit. It does not mean autonomous runs work. Existing-loop/provider composition and live development runs are a separate next task; do not enable daw start/continue here.
