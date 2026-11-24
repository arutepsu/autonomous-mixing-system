# R2A deterministic diagnosis readiness — implementation handoff

> **For agentic workers:** Use superpowers:executing-plans; delegate only if separately requested. Implement tests first. Leave everything uncommitted for Codex review.

**Goal:** Implement the real diagnosis-source boundary over the reviewed R1 measurement bindings, reporting incomplete P1 qualification and unavailable P2/P3 without fabricating candidates.

**Architecture:** Immutable configuration/bindings in core.records.masking_diagnosis; pure diagnosis in core.diagnosis.masking; ConfiguredDiagnosisSource in drivers.diagnosis_runtime. Core reads no files, arrays, provider or DAW. The driver placement preserves the rule that an adapter cannot import a diagnosis stage.

**Tech stack:** Existing Python/std library and core records/ports; existing R1 analyzer/preparation components only in integration tests. No new dependency.

**Spec:** docs/superpowers/plans/2026-11-20-diagnosis-and-decision-adapter.md (R2); docs/superpowers/specs/2026-11-20-p1-diagnosis-method.md (especially sections 5–7 and the R1 implementation note). Starting HEAD bb3e0a5, clean tree. Read current instructions/status before editing.

## Scope decision

This is **R2A, not completion of all R2**. The accepted method explicitly requires incomplete P1 diagnosis before numerical policy approval. Existing QualificationStatus has only UNCALIBRATED, PreparedSupport is geometric U, and every qualified metric is Unavailable. A record constructor or configured threshold cannot turn U into qualified S0.

R2A implements useful evidence admission/readiness and the source boundary now. R2B needs approved qualification/numerical policy and a reviewed extension producing genuine S0 and qualified R1 metrics, before positive candidate generation can be implemented. Do not quietly build a calibrated/qualified route, add arbitrary cutoffs, or test a fabricated runtime diagnosis to claim R2 finished.

Dataset creation in FL Studio continues independently. Paid/professional listeners, the optional six-project study and fixed parameter grids are not prerequisites for this software slice. R2A does not establish a detector, musical improvement, or readiness for autonomous corrective runs.

## Global constraints

- No provider/FL Studio calls, live trials, installation, saving/exporting, CLI enablement, coordinator wiring or commits.
- No changes to reviewed DSP kernels, R1 metric meanings, qualification enums, evidence-manifest format, mono profile or R3/R4 packet/parser behavior.
- Keep existing DiagnosisSource and DiagnosisRequest contracts. Do not add a manual decision-file source or return an AI decision from diagnosis.
- No raw descriptive metric substitution for qualified metrics; no margin/median/exceedance sign treated as audible masking or a mixing defect. P2/P3 remain UNAVAILABLE.
- No calibration or hidden-reference files in runtime configuration. Policies/bindings carry public method/support identity only, not evaluation material. Producer preferences do not alter diagnosis.
- Valid unavailable qualification returns a truthful report, not an infrastructure failure. Invalid configuration/wiring fails distinctly; unexpected programming errors propagate.

## Review focus

1. A complete set of finite raw observations is incorrectly promoted to sufficient qualified evidence.
2. Same cell count hides different support, or (A,B) silently matches (B,A).
3. Several checkpoint members match a source requirement and the implementation silently picks the first or invents a latest-revision rule.
4. Missing/ambiguous/wrong-version evidence produces ASSESSED/all-clear or a placeholder candidate.
5. The configured source becomes a labelled test double that ignores real input, rather than validating the actual bindings.

## Task 1 — Validated immutable diagnosis configuration

**Create:** src/autonomous_mixing/core/records/masking_diagnosis.py and tests/unit/test_masking_diagnosis_contract.py.

**Interfaces:** MaskingDiagnosisJob and MaskingDiagnosisPolicy. A job holds the configured ordered pair, prepared-support identity and exact MeasurementDefinition bindings. The policy holds its method VersionRef, the full MaskingMeasurementPlan and a nonempty tuple of jobs. All required fields are explicit; no numeric thresholds, approved flag, arbitrary confidence or default qualified mode.

- [ ] Write failing construction tests before implementation.
- [ ] Reuse MaskingPair, PreparedSupport/MaskingMeasurementPlan, MeasurementDefinition and existing source/scope validators. Do not copy NumPy/DSP logic or import an adapter for metric constants. Use versioned record-level metric identities and exact units; any new metric catalogue constant must agree with R1 in a test.
- [ ] Require each job to identify an existing support in that exact prepared plan by full direction, configured region/interval and digest. Validate the full binding, not just a ref or cell count. Plan specification must retain its prepared.sha256 binding.
- [ ] Pin the required qualified catalogue for readiness: p1.support_cells@1 (cells), p1.robust_exceedance_cells@1 (cells), p1.longest_robust_run_seconds@1 (s). They all share exact configured scope, specification, analyzer and ordered sources. Validate definition alignment against prepared-support alignment here; Observation has no independent alignment field. Reject raw IDs as substitutions and duplicate/conflicting job/metric bindings.
- [ ] Keep profile source geometry consistent with R1 native mono 44.1 kHz PCM_16 requirements. Do not interpret nominal band frequencies as exact bin coverage, include band 0, discover regions or change the selected support.
- [ ] Test wrong plan/support digest, swapped direction, changed cells at equal counts, units, versions, analyzer, alignment, missing/duplicate bindings and out-of-scope jobs. Empty geometric support is valid unavailable readiness, not an invented positive count.
- [ ] Implement minimally, run focused tests, and update the records/import inventory explicitly.

## Task 2 — Pure readiness diagnosis

**Create:** src/autonomous_mixing/core/diagnosis/__init__.py (docstring only if absent), masking.py, tests/unit/test_masking_diagnosis.py.

**Interface:** diagnose_masking(request: DiagnosisRequest, *, policy: MaskingDiagnosisPolicy, identities: IdentitySource) -> DiagnosisReport.

- [ ] Write red tests for real request/definition matching before coding the function. Structural fixture measurements are labelled tests, never evaluation data or claimed model/detector outputs.
- [ ] Validate configured jobs against the request's project state and scope. Resolve each SourceRequirement to exactly one checkpoint member matching every requirement field (kind, ordered contributor tracks, render specification, rate, channels, timeline start and frame count), matching R1 single_member semantics. Zero/multiple matches mean incomplete readiness; never pick the first or infer latest by revision. Compare observations' ordered artifact IDs against the resolved IDs. An older production revision alone is not stale, and baseline artifact IDs/hashes in PreparedSupport remain preparation provenance rather than a prohibition on legitimate changed candidate sources with compatible geometry.
- [ ] Match observation metric/scope/specification/analyzer and those ordered sources exactly. Check result units for MeasuredScalar only. Unavailable has no unit; preserve its typed unavailable status instead of reporting a nonexistent unit mismatch. The pure rule does not independently verify file bytes, manifest authenticity or physical alignment: alignment is the checked configuration declaration, and observations provide only their existing spec/analyzer/source provenance.
- [ ] For evidence collection, explicitly distinguish missing, duplicate/ambiguous, mismatched, unavailable and present observations. An observation for the reverse direction or another prepared specification never fills a missing job. Unrelated valid observations may coexist; they do not satisfy these requirements. Invalid configuration/request scope is a wiring error; missing or unusable measurement evidence is reported as incomplete readiness.
- [ ] Return one P1 coverage row for the request scope/method, status INCOMPLETE, and no candidates. Its deterministic explanation records the calibration/S0 gap plus each job's evidence-readiness findings. Several jobs still share that one overall P1 row, avoiding duplicate coverage keys; job direction/region/support are stated explicitly in local explanation. Never imply that assessed-empty/all-clear occurred.
- [ ] Even if a caller supplies finite qualified-named scalars, the current uncalibrated geometric plan cannot establish qualification: P1 stays INCOMPLETE, with no candidates. Do not trust a flag or reconstruct qualification from raw summaries. Qualification remains unimplemented.
- [ ] Return separate FREQUENCY_BUILDUP and PHASE_INTERACTION coverage rows with UNAVAILABLE, method=None and explicit unsupported-method explanations. No problem candidate of any family is emitted in R2A, including insufficient-evidence placeholders.
- [ ] Generate fresh valid DiagnosisCoverageIds through IdentitySource, checking distinctness; duplicate/invalid tokens are wiring errors. Reports are deterministic apart from IDs. Validate reports with validate_diagnosis_response in tests and through the source wrapper.
- [ ] Test silence, zero support, raw-only evidence, qualified Unavailable observations, absent measurements, duplicate matching observations and changed current revisions. Test repeat classification, independent requests, and no mutation of inputs.
- [ ] Pin at contract level that DiagnosisRequest has no producer-preference field; do not build extra machinery to inject one. Qualitative preferences belong to the decision stage.
- [ ] Implement minimally, verify red-to-green and update stage behavior inventories. No imports from core.verification/admission/loop or adapters.

## Task 3 — Configured source and genuine R1 integration

**Create:** src/autonomous_mixing/drivers/diagnosis_runtime.py and tests/unit/test_diagnosis_runtime.py.

**Interface:** ConfiguredDiagnosisSource(*, policy: MaskingDiagnosisPolicy, identities: IdentitySource), with diagnose(DiagnosisRequest) -> PortResult[DiagnosisReport]. It invokes the pure rule, validates with validate_diagnosis_response, and returns Success(report). No default production policy or test switch.

- [ ] Write red wrapper and integration tests. Test exact delegation/input sensitivity and validation, not a hardcoded canned report. Missing calibration is a successful incomplete report. Do not catch programming/configuration errors as retryable provider failures.
- [ ] Use existing R1 test builders and real preparation/analysis/router on temporary native-profile WAVs to produce descriptive observations and qualified Unavailable results. Feed the actual result into the configured source and obtain INCOMPLETE P1 / UNAVAILABLE P2/P3 / zero candidates.
- [ ] Test both pair directions, current-state replacement artifacts, changed prepared support and malformed/missing evidence. Do not fabricate successful qualified output from the real analyzer.
- [ ] Keep the source constructible on its own. Do not register it in daw_runtime/main, enable CLI start/continue, call Claude, or compose a new loop. R5 remains separately reviewed; R2A alone cannot justify corrective execution.
- [ ] Add architecture isolation proving core diagnosis loads no third-party libraries/files/provider code and drivers remain the sole composition boundary.

## Task 4 — Verification, documentation and review

- [ ] Add permanent regressions and mutation probes for promoting raw to qualified, treating finite scalars as qualification, omitting direction/support/current-source checks, selecting the first ambiguous observation, making P1 ASSESSED, and emitting a placeholder candidate. If a later invariant also catches a mutation, assert the intended boundary where meaningful; label equivalent/no-effect mutations rather than inventing a success percentage.
- [ ] Run all targeted tests with uv run --locked --extra agent pytest ... -W error, then the full suite, ruff check ., format check, CLI help, uv lock --check and git diff --check. Do not change unrelated tests or accepted ADRs to make the new slice pass.
- [ ] Append the research log and update the R2 section's status as **R2A implemented; qualified candidate generation and R2B remain pending**. Document exact public signatures, binding rules, observed red/green checks and limits. No status claiming a qualified detector or autonomous system.
- [ ] Leave all work uncommitted. Report the next prerequisite: approved numerical/qualification policy plus a separately reviewed R1 qualified-support/metrics extension, followed by R2B candidate-rule implementation and R5 composition. No scientific thresholds are approved by these software tests.

## Completion boundary

The deliverable is a real, input-validating diagnosis source that explains why current evidence cannot establish a qualified P1 candidate, not a functioning positive detector. It is useful infrastructure while the owner creates the dataset; it does not resolve missing scientific calibration. No additional corpus/listener workload is imposed by this handoff.


## Planning builder check (2026-11-22)

A fresh read-only check confirmed the bounded slice and clarified two existing-record limitations: source matching must be unique over all SourceRequirement fields rather than a latest-revision heuristic, and observations cannot independently authenticate alignment or supply a unit for Unavailable. Both clarifications are included above. No implementation or additional scientific/data requirement was introduced.
