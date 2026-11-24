# Claude handoff: P1 calibration registration

**Task:** Documentation only. Prepare a reviewable calibration-registration protocol before R2 diagnosis. Do not implement calibration or diagnosis and do not run experiments.

**Superseding owner clarification (2026-11-22):** the resulting six-project/three-listener/grid-search protocol is now an optional extended-study proposal, not the required thesis path. Prioritize agent/tool/loop evaluation within available resources; do not treat the original handoff as approval of its workload. See the registration revision 3 and the implementation plan's thesis-focus clarification.

**Starting point:** R1 is committed at `0242c99` (`feat(analysis): add descriptive P1 directional measurements and fixed support`). Confirm current status before working and preserve unrelated changes.

## Read first

- `docs/superpowers/specs/2026-11-20-p1-diagnosis-method.md`, especially sections 4, 6 and 8.
- `docs/superpowers/plans/2026-11-20-diagnosis-and-decision-adapter.md`.
- `docs/research/methodology.md` and `docs/research/dataset-plan.md`.
- The front-end numerical contract and the R1 records/preparer/analyzer. Distinguish implemented raw geometric support U from unavailable qualified support S0.
- Applicable repository instructions and documentation policy.

## Deliverables

Create `docs/superpowers/specs/2026-11-22-p1-calibration-registration.md` with status Proposed, a revision, the exact code baseline and explicit approval/run/freeze gates. Append a short research-log entry. No requirement-status change or ADR adoption by implication.

### 1. Claims and separate calibration tracks

Separate:

1. Numerical reproduction error in T, E and M against the pinned independent captures/reference.
2. Sensitivity to PCM_16 quantization and low-energy/quiet-floor conditions.
3. Development selection of the evidence-sufficiency rule.
4. Later independent evaluation of perceptual or producer-intent outcomes.

Numerical agreement does not validate perception. A positive model margin does not establish an undesirable mixing defect. Do not reuse the trap pilot's thresholds or the descriptive 0.005552 dB residual as approved parameters.

### 2. Corpus and provenance registration

Inventory only existing documentation/metadata needed to establish usable material. Do not audition, analyze or tune against held-out material. Record known facts and unresolved owner inputs separately:

- underlying musical project/song ID, source provenance, rights evidence for the intended research use, native format and alignment provenance;
- development/calibration or held-out assignment at song level, with all derived variants in the same split;
- producer intent and role declarations, when available;
- immutable hashes and the point at which each manifest will be frozen;
- synthetic numerical controls, clearly separate from musical evaluation items.

The first profile remains native aligned mono 44.1 kHz PCM_16. Existing stereo exports do not become eligible by assumption. No conversion profile is introduced. Missing usable corpus or rights information is a blocking registration item, not an invented dataset row or a reason to substitute synthetic evaluation music.

### 3. Exact numerical-control matrix

Specify future inputs, generation recipes/seeds, reference acquisition, compared stages, regions and expected diagnostic relationships before execution. Include near-zero margins, exact silence, quiet-floor ties, low-level decay, phase/gain variation, re-quantization, startup/short-path history and full-window boundaries.

Define how numerical residuals determine a declared domain and conservative `delta_M`/quiet-elevation allowance. State the calculation and selection rule in advance; do not simply inspect results and choose a favorable margin afterward. A finite measured maximum is not a universal proof. Include failure rules when the proposed validated domain cannot be established.

Use all natural FFT bins 0-512, including Nyquist, for the rounding-noise reference. Treat the uniform-noise model as a diagnostic reference, not the realized quantization error or a validated audibility floor.

### 4. Finite parameter search and selection rule

Provide explicit proposed candidate values or deterministic data-derived rules for:

- target and masker low-energy qualification margins;
- numerical/quiet-elevation allowances;
- `n_min`, `p_min` and `duration_min_seconds` from the accepted candidate-rule form.

For each, label the basis: source-supported constant, project proposal or data-derived value. Proposed search values are not calibrated results. If a value genuinely cannot be specified from available information, name the dependency and block that part of registration rather than writing a vague instruction to tune it later.

Specify the total search size, trial order, objective, tie rule, fixed stopping criterion, invalid/incomplete run handling and rejection of degenerate rules. Log every tested configuration. Do not optimize only the same model score and then claim independent scientific validation. If the selection objective needs human assessment, specify the development annotation protocol and label storage boundary; do not pretend those labels already exist.

### 5. Frozen support and comparison semantics

Register the exact lifecycle from verified initial input to S0 preparation, final bound specification and run construction. R1 currently prepares only U and returns qualified metrics as Unavailable; additional implementation to produce qualified S0 must be separately reviewed after parameter selection.

Retain exact cell identity across comparisons. Specify treatment of target silence, masker silence, quiet-floor transitions, low-energy invalidity, gaps and unavailable cells. No denominator shrinkage, region search or post-hoc removal of adverse cells. Band 0 remains descriptive and excluded from the first frequency-specific diagnosis set.

### 6. Hidden-reference boundary

Describe permitted runtime roots and separate evaluation/calibration stores. Hidden clean/manual references, final labels and reference-derived targets never enter runtime observations, diagnosis requests, model prompts or feedback. Development labels may support offline policy selection only under the registered split; the frozen policy and its provenance may enter runtime, the labels themselves may not.

Preserve the distinction between known numerical controls and hidden musical references. Specify sentinel/leakage tests for the later implementation. Do not assume a prompt warning establishes isolation.

### 7. Approval, execution and freeze gates

Use distinct states:

- Proposed protocol: no experiment authorized.
- Registration approved: inputs, search and decision rules are fixed; experiment execution still needs explicit authorization.
- Calibration run completed: all raw results and attempted configurations retained, including failures.
- Policy frozen: selected values, claim limits and independent scientific review recorded.
- Held-out evaluation authorized: separate from calibration and implementation approval.

A failed calibration leaves diagnosis insufficient/unavailable. There is no fallback that invents a candidate merely to enable the agent. An approved registration is not approval of missing numerical results.

### 8. Reproduction package and honest limitations

List exact manifests, code/method versions, source hashes, reference captures, request/settings records, raw outputs, search log, timing and approval records to retain. Specify how another operator can reproduce the numerical calculations without contacting an AI provider or opening FL Studio.

Identify source-page verification still needed, corpus/rights gaps, sample-size limits, proxy-versus-perception limitations and conditions that would invalidate the run. Use primary sources for scientific claims; do not substitute a withdrawn related paper for the cited thesis.

## Completion report

Return the document path, proposed decisions requiring review, known versus missing corpus facts and the exact checks actually performed. Do not claim calibrated thresholds, validated diagnosis or evaluation readiness. Leave documentation uncommitted for Codex review.

**Explicitly out of scope:** implementation, dependency changes, downloads of evaluation audio, experiment execution, parameter fitting, new corpus generation for evaluation, diagnosis candidates, provider calls, coordinator changes, live FL Studio, saving/exporting or committing.
