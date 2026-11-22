# P1 directional masking: measurement and diagnosis method

**Status:** Method specification accepted by the owner, 2026-11-20. Updated 2026-11-22: R1 descriptive measurement/support implementation is reviewed and committed at `0242c99`; calibration has not run, qualified support S0 is unavailable and production diagnosis is missing. Design acceptance does not establish numerical policy values or perceptual validity.

**Parent:** [Diagnosis and AI decision adapter plan](../plans/2026-11-20-diagnosis-and-decision-adapter.md).

**Purpose:** Define reproducible pairwise model evidence and the conditions under which it may support an uncalibrated risk candidate. Do not claim audible masking, a mixing defect, or perceptual improvement from model exceedance alone.

## 1. Scope and source ownership

The first profile is two aligned, native mono 44,100 Hz PCM_16 clips of equal length, analyzed at their actual exported levels. Read counts exactly through the existing verified WAV boundary. No resampling, normalization, stereo fold-down, bit-depth conversion or summation of several maskers is part of this profile. Stereo masters may still be measured by the separate native-channel level analyzer, but are not P1 inputs.

The [front-end numerical contract](2026-11-11-mpeg-psychoacoustic-front-end-contract.md) owns DSP equations, tables, precision, captures and discrepancies. This document owns selection, aggregation, evidence sufficiency and calibration. Reviewed numerical kernels are reused unchanged. P2 buildup and P3 phase interaction remain unavailable.

| Basis | Supported use | Limit |
| --- | --- | --- |
| Ronan, *Intelligent Subgrouping of Multitrack Audio* (2018), section 7.2.4.2, equations 7.11-7.13, traced in the existing front-end contract | Cross-adaptive threshold-to-target ratio; a separate positive-margin aggregation normalized by 20 dB | Does not supply our temporal aggregation, activity gate, diagnosis cutoff or perceptual calibration. The chosen dist10 backend is not established as Ronan's implementation. |
| Pinned dist10 implementation and existing Probes B-H | Concrete numerical grid, history, band mapping and reference comparison | Reproduction of this implementation is not validation of its accuracy for musical perception. |
| Hafezi and Reiss (2015), *Autonomous Multitrack Equalization Based on Masking Reduction*, DOI 10.17743/jaes.2015.0021 | Motivation for separating objective masking estimates and subjective evaluation | Supplies no transferable threshold for this method. The paper cautions about objective/perceived masking agreement. |
| Project policy in this document | Fixed support, conservative qualification, scalar summaries and candidate rule form | These are experimental choices requiring review and development calibration, not published psychoacoustic facts. |

Primary links: [Ronan thesis repository](https://qmro.qmul.ac.uk/xmlui/handle/123456789/55527), [Hafezi and Reiss publisher record](https://aes.org/publications/elibrary/elibrary-page/?id=17637), [author-hosted paper](https://www.eecs.qmul.ac.uk/~josh/documents/2015/Hafezi%20Reiss%20-%202015.pdf).

Source-check note (2026-11-20): the equation/page trace above reuses the repository's existing thesis reading; the browser did not provide a readable full thesis PDF in this task. Do not report a fresh independent full-text verification. The related [arXiv:1803.09960](https://arxiv.org/abs/1803.09960) is marked withdrawn; it is not used here as independent validation or substituted for the thesis. Recheck and retain the cited thesis pages before final method freeze.

## 2. Pair identity and physical alignment

Name each direction explicitly: A is masker, B is target. Compute B -> A as a different job, never infer it by changing a sign. Require:

- `AnalysisScope.track_ids == (A, B)`; ordered source requirements and resulting artifact IDs use that same order. Preserve it through serialization and matching. Opposite directions then have different existing metric/scope keys.
- Exact input sample rate, sample count and project timeline start agreement. No padding, truncation, cross-correlation shift or offset correction inside the analyzer.
- An input-provenance record describing how the common sample origin was established (for example a documented common export selection). Hashes and metadata alone do not prove physical synchronization.
- Unknown alignment provenance yields unavailable P1 evidence. A declared common origin remains declared: the record must not claim sample-level authentication of a DAW project or export procedure.

Per-file hash, artifact revision, export settings, declared timeline, alignment provenance and source order are retained. Only current runtime source roots may be resolved. The experiment's clean/manual references and their labels are not eligible sources.

## 3. Numerical grid and directional margin

Run each entire clip from call 0 through `dist10_clip_to_bands`. Do not crop to the requested interval before processing; predictor and threshold histories require the prefix. For N samples, a call k is eligible exactly when `k >= 2` and `576*k + 256 <= N`. Its source window is `[576*k - 768, 576*k + 256)`.

For a requested time interval, select only eligible calls whose complete source windows lie inside that interval in project sample coordinates. Reject interval boundaries that cannot be represented as integral sample positions under the declared profile rather than silently rounding them. This conservative complete-window rule is a project choice; it reduces boundary contamination and may leave a short interval unassessed.

For each eligible call and long band b:

- `T_A[k,b]` is A's final band threshold;
- `E_B[k,b]` is B's band energy;
- `M_A_to_B[k,b] = 10 * (log10(T_A[k,b]) - log10(E_B[k,b]))` in dB, using the reviewed margin kernel.

Positive M is model-threshold exceedance, zero is equality, negative is energy above that threshold. Use log subtraction, not a potentially overflowing ratio. Non-finite/unavailable cells are not replaced with zero. Digital silence is detected from the raw window's nonzero count, not inferred from the model's positive FFT floor.

### Band selection

Persist all 21 model bands descriptively. For the first frequency-specific diagnosis profile, propose bands 1-20 only. Band 0 is excluded because dist10 also sends bins 465-512 into its partition-0 contribution. Exclusion does not repair or renormalize the model; report the missing coverage explicitly. In particular, this profile cannot claim complete low-bass masking coverage.

Regions are explicit fixed model-band index lists, chosen in run configuration before examining correction outcomes. Use actual mapping provenance and nominal labels; do not call a nominal frequency boundary a brick-wall filter. Reject arbitrary frequency regions that cannot be represented by the selected whole bands. No automatic search for the most favorable interval/band group in this first profile.

### Quiet floor

Define `Q[b]` by applying the existing band mapping to the pinned partition `qthr` table. It is a per-profile constant vector, not an estimate fitted to the recording. Compare the actual T with Q; do not subtract Q and then pretend the resulting ratio is the original metric.

A threshold equal to Q provides no evidence that the modeled source elevated it above quiet. A threshold above Q is described as model elevation above quiet, not proof of perceptual causation. A band can mix quiet-limited and signal-limited partitions; this simple band comparison does not identify their separate shares.

Current `ClipBandAnalysis` already exposes energy, threshold and silence metadata but not Q or partition attribution. R1 must derive Q through the existing tables/mapping and retain its provenance. No partition-share field is assumed to exist.

## 4. Support and qualification: never improve by dropping cells

Separate three sets:

1. **Geometric support U:** declared band indices crossed with eligible calls whose full windows lie in the declared interval. Its identity is fixed by profile, pair, timeline/sample geometry and region.
2. **Initial qualified support S0:** cells in U passing the reviewed qualification policy on the initial runtime mix. Freeze the exact cell list and digest before any correction is proposed.
3. **Current qualification flags:** report the same predicates on a later state for diagnostics; these never silently redefine S0.

The initial runtime mix is the producer-supplied input, not a hidden clean reference or a manually corrected target. Persist the S0 manifest and method parameters in a content-addressed runtime record; a resumed run must recover and verify the same manifest, never recompute it from a candidate.

### Preparation lifecycle (explicit contract, proposed)

Existing `RunRequest` fixes its `AnalysisSpec` and measurement definitions before execution. Do not mutate them after an initial analysis. Use a distinct deterministic preparation request/result before constructing the run:

1. `MaskingPreparationRequest` names the initial runtime state and allowed roots, profile, ordered pairs, regions, frozen qualification policy and audio-read/resource limits. It contains no producer/reference target or AI proposal.
2. Preparation verifies and measures that initial state, derives S0 and writes a content-addressed `MaskingPreparedPlan` manifest with the exact baseline hashes, policy, source geometry, cell list and evidence hashes. It records timing, outcome and source provenance. No diagnosis or decision runs here.
3. Only a successful, completely persisted result can be used to construct the final `AnalysisSpec`, definitions and `RunRequest`. Their final specification identity binds the prepared-plan digest; the configured analyzer registry resolves only that exact immutable plan.
4. The coordinator's ordinary initial analysis checks the original input hashes, consumes the frozen plan and emits correctly bound observations under the already-final identity. Restart verifies and reloads that same plan. Missing or changed preparation evidence refuses analysis rather than regenerating support.

R1 must define and test these preparation records and the pure preparation/calculation interfaces; adapter code owns verified reads and persistence. R5 composes preparation before run construction. This is an explicit addition, not a hidden mutation of core configuration or a second decision loop.

There is currently no analysis-call count in `BudgetOperation`. Preparation therefore has no fictional analysis-operation charge. Bound it by explicit audio-read/resource limits and cancellation/time checks; record its elapsed time separately from the coordinator's run budget. Scientific comparisons must give conditions the same prepared support and report preparation cost plus run cost, not hide preprocessing. The ordinary initial analysis remains timed by the existing coordinator. Any later proposal to add a counted preparation/analysis budget needs its own contract review.

### Proposed initial qualification predicates

All are experimental screening rules, not audibility tests:

- Both raw windows contain nonzero samples.
- Target energy is reliably above the PCM_16 quantization sensitivity region for that band.
- Masker-band energy is reliably above that sensitivity region, and T is reliably above Q.
- The selected band belongs to the supported diagnosis set and the numerical error envelope applies.

A local masker-band gate is conservative: it may exclude cross-band spreading from energy in neighboring bands. Report this false-negative limitation rather than describing the gate as part of Ronan's formula.

Keep separate exclusion flags; a cell may have more than one. Empty S0 yields unavailable summaries, not zero masking and not a successful condition.

On a later state, evaluate every cell of S0 even when a margin becomes negative. A masker becoming exactly silent or quiet-floor-limited can be an actual reduction: retain its finite M and record the flag; do not delete it. If the target becomes silent, values leave the validated low-energy regime, or required numeric evidence is unavailable, mark the fixed-support comparison unavailable. Never reward target muting, changed denominators or unquantifiable low-energy values. Report exploratory current-state summaries separately under different metric IDs.

The exact-silent masker case is a separately verified quiet-floor control, not an extrapolation of a near-noise nonzero masker. Nonzero low-energy masker cases outside the calibrated regime make sufficiency unavailable; do not treat them as exact silence.

The actual sensitivity thresholds and the numerical deadbands remain uncalibrated (section 8). Until approved values exist, R1 can report geometric/raw descriptive measurements; it cannot claim qualified production evidence or an actionable diagnosis.

## 5. Scalar observation catalogue

Use versioned metric IDs, immutable definitions and exact units. All summary metrics bind the same ordered sources, region, analysis specification and support-manifest digest. Different support identities are incompatible, even when their cell counts happen to match.

| Proposed metric name (version allocated at implementation) | Definition | Unit/availability |
| --- | --- | --- |
| `p1.support_cells` | `size(S0)` | count; no negative/fractional counts |
| `p1.signed_margin_median_db` | Ordinary median of M over S0; even count is mean of the two middle values | dB; unavailable for empty/invalid support |
| `p1.positive_margin_cells` | Count of `M > 0` over S0 | count; descriptive, strict comparison |
| `p1.positive_margin_fraction` | Positive count divided by `size(S0)` | fraction in [0,1]; not a probability of audibility |
| `p1.robust_exceedance_cells` | Count of `M > delta_M` over S0 | count; unavailable until applicable error allowance is frozen |
| `p1.longest_robust_run_seconds` | Longest consecutive robust-exceedance run in any one selected band, with gaps breaking a run | seconds; unavailable until applicable error allowance is frozen |
| `p1.mean_positive_margin_db` | `sum(max(M,0)) / size(S0)` | dB; zeros from nonpositive cells stay in the denominator |
| `p1.comparison_supported` | Not proposed as a numeric truth flag: use typed availability plus the support digest and audit record | No success encoded as a fabricated scalar |

These are project temporal/band aggregations with equal cell weight; they are not claims about independent samples or equal perceptual importance of bands. In particular, a median can hide localized exceedance, so retain counts and the diagnostic grid rather than using it as a sole detector.

A separate descriptive Ronan-style trace may report `R[k] = sum_b(max(M[k,b],0)) / 20` for a specified band set. The 20 dB divisor comes from the thesis trace; do not clip individual margins at 20 dB. Any temporal mean of R, excluded bands or gating is a project extension, explicitly named. It is not interchangeable with the median or an auditory probability, and is not the first diagnosis acceptance criterion.

Raw grids, valid/qualification flags, exact cell IDs, Q, exclusion reasons and source/method hashes remain adapter-local evidence artifacts. Core observations remain scalars or unavailable. Core diagnosis receives verified metric/support bindings through versioned plan/policy records, not NumPy arrays or filesystem access.

## 6. Candidate rule and semantics

A candidate means: **persistent, qualified model-threshold exceedance in the declared pair/region under this experimental policy**. It does not mean a defective mix. Producer intent affects whether the AI proposes any correction, not whether the measurement rule changes its answer.

Proposed rule form, fixed for calibration:

1. All required metrics, current source bindings, method versions and support identities match the policy, with enough qualified evidence.
2. `size(S0) >= n_min`.
3. The robust exceedance fraction `count(M > delta_M) / size(S0) >= p_min`.
4. At least one selected band has a run of robust-exceedance cells lasting at least `duration_min_seconds` under the exact call grid.

Missing/invalid cells break a run; no bridging over gaps, median smoothing or extrapolation. Duration is the number of consecutive calls times `576/44100` seconds, a declared grid convention rather than independent listening duration. `p_min`, `n_min`, `duration_min_seconds` and the applicable deadband require the protocol below; they are not chosen from the trap pilot or supplied by the AI.

Once scientifically approved, emit one candidate per configured pair/region meeting the rule, citing the scalar observations, method, support and a concise account of which predicates were met. Do not discover regions from the same data and omit the selection procedure. Representation remains `UNCALIBRATED` or qualitative as the existing contract permits; no calibrated numeric confidence is invented.

Before approval of the rule's numerical parameters, P1 diagnosis is INCOMPLETE and cannot authorize correction. Prefer explicit incomplete coverage with no actionable candidate over a placeholder candidate. After approval, assessed-empty means only that this configured rule did not trigger. P2/P3 remain UNAVAILABLE, not healthy.

## 7. Separate diagnosis, objectives and correction claims

The rule consumes `p1.support_cells`, `p1.robust_exceedance_cells` and `p1.longest_robust_run_seconds` plus availability/support bindings. The measurement adapter computes the run statistic; core diagnosis does not reconstruct it from a scalar median or read the grid.

The rule above is producer-independent. The decision receives the rule's limitations, producer intent, protected tracks, permitted absolute application-gain bounds and all runtime objective/guard thresholds. A qualitative preference is not automatically converted into a numerical threshold.

Candidate sufficiency and correction acceptance are different policies. A candidate can exist without a justified permitted action. A lower model margin can result from source-level changes; report target/masker level effects and native-channel master/stem guards separately. Do not optimize only the proxy and then cite that same proxy as independent perceptual validation.

NoChange, abstention, missing support and failed analysis do not establish target achievement. Runtime verification retains the existing criteria and exact source/specification matching. All hidden clean/reference audio, grading labels and manual-reference targets remain outside runtime inputs and prompts.

## 8. Calibration and registration protocol

### 8.1 Numerical reproduction, independent of a musical diagnosis

Before collecting held-out outcomes:

1. Register the profile, algorithms, source/fixture hashes, generated-control seeds, bands and test matrix.
2. Compare complete-chain T, E and M with captured reference values on existing fixtures and additional boundary-focused controls. Include near-zero margins, low-energy decay, startup/short-path calls, quiet-floor ties, exact silence, quantization-adjacent values and geometric boundaries.
3. Measure energy/threshold residuals and their propagated margin error. A single maximum from a finite corpus is empirical evidence, not a proof for arbitrary int16 audio. State the validated domain.
4. Select and record `delta_M` and a quiet-elevation allowance from those results plus a declared conservative allowance, before evaluating diagnosis performance. Cells outside the validated energy regime are unqualified; do not extrapolate the tolerance.
5. Test signs near the boundary. Values within the numerical deadband are indeterminate for robust exceedance, while their raw signed value remains available descriptively.

The existing 0.005552 dB margin difference is one descriptive result. It is not preselected as delta_M. No tolerance is inferred from the intended size of a gain cut.

### 8.2 Quantization sensitivity

Use a declared uniform-rounding-noise reference only as a diagnostic model: per-bin expected unnormalized energy `(1/12) * sum(window**2)` in int16-count units, mapped through all natural bins 0-512 and the existing partition/band mapping. Nyquist must be included. This does not assert that the actual error is independent white noise or measure its realized spectrum.

Test this reference against actual re-quantization controls, including quiet decays, different phases and gains, with no changes in timing. Record sensitivity of M and candidate predicates. Choose target/masker qualification margins only if these controls support stable behavior. The pilot's 10/20/40 dB descriptive groups are not validated gates. Mono conversion contributes another rounding and is absent from this first native-mono profile.

### 8.3 Evidence-sufficiency policy

Pre-register a development corpus manifest with rights, genre/source provenance, song-level splits and documented alignment. Synthetic tones/noise/impulses are numerical controls, not musical evaluation data. Determine `n_min`, `p_min` and minimum duration from development evidence under a recorded, bounded candidate-parameter search; log every tried configuration, not only the best.

The registration must specify candidate values, selection objective, tie handling and a fixed stopping rule before the calibration run. No numerical values or empirical result are fabricated in this document. Require independent human assessment on development excerpts if the intended wording extends beyond model exceedance. Without such evidence, the rule may only be approved as a limited model-risk heuristic, never a validated perceptual detector.

Freeze the selected policy, uncertainty wording and calibration results before held-out testing. Do not search again because held-out performance is disappointing. A method revision requires a new version and a fresh evaluation allocation.

### 8.4 Reproducibility record

Retain source hashes; dataset/role/split manifests; profile, method and support IDs; exact calibration inputs/commands; raw grids and summaries; tested parameter configurations; numerical errors; unavailable counts; annotation protocol where used; frozen choices; and reviewer approval. Preserve original files. Reference and annotation material stays in the evaluation store and cannot be resolved by runtime roots.

## 9. Software review tests required by this method

These specify future tests, not tests run in R0:

- Opposite directions coexist in one analysis request without deduplication or a source-order swap.
- Exact input counts, unchanged full-clip histories, eligibility and complete-window interval selection match the captures.
- Silent target, silent masker, quiet-floor-only threshold, short interval and empty support are distinguished.
- Band 0 remains descriptive, excluded from the first frequency-specific candidate policy, and never relabeled as isolated bass.
- Same count/different cell IDs is refused as a support mismatch; candidate activity changes cannot silently improve a denominator.
- Current target silence/low-energy invalidity makes fixed-support comparison unavailable; a valid masker reduction retains every original cell.
- Strict positive, robust-positive and deadband cases differ as specified. No 20 dB clipping or accidental positive-only denominator is introduced.
- Missing/wrong units, source bindings, policy/spec versions, alignment provenance or support digest refuse sufficiency.
- Empty candidates or an unsupported family never imply success. Producer preference and hidden-reference changes cannot alter deterministic diagnosis.

## 10. R0 exit conditions and next action

This specification freezes the proposed structure and explicitly exposes what is not yet calibrated. R0 is not scientifically complete merely because this file exists.

**Reviewable now:** mono profile; exact directional and timeline semantics; all-band descriptive evidence with bands 1-20 as the first frequency-specific diagnosis set; fixed initial support; scalar formula definitions; candidate-rule form; and separate numerical/qualification/policy calibration.

**Required before qualified/actionable diagnosis:** retained source-page verification, approved calibration registration (including finite candidate parameter choices), numerical results, selected qualification/deadband/sufficiency values, a frozen method digest, and scientific review of the precise claims. Until then, do not set `insufficient_evidence=False` on a placeholder diagnosis.

After review of this specification, R1 can implement descriptive measurement and support-manifest mechanics without inventing gate values. Qualified summaries and R2 actionable risk candidates remain gated on the calibration decisions. This staged restriction does not prevent R3/R4 provider engineering tests with explicitly labelled contract fixtures; it prevents them being presented as scientific diagnosis or an autonomous mixing trial.

## R0 review record

A read-only builder check found three data-path gaps: a missing scalar for temporal persistence, a support-manifest lifecycle incompatible with fixed run configuration, and an incorrect claim of an existing counted analysis budget. This draft now exposes the run-duration scalar and proposes a separate persisted preparation phase before run construction, with bounded resources and separately reported timing. These are design proposals, not implemented behavior.

## R1 implementation record (2026-11-20, Claude Code; review completed and committed 2026-11-22 at `0242c99`)

This section records what R1 implemented and where it differs from or makes precise the design above. R1 and its four review fixes were reviewed before commit `0242c99`; the implementation decisions below record that descriptive slice. Qualified support, diagnosis and numerical calibration values remain unimplemented/unestablished. Independent focused re-review ran 344 tests with warnings as errors; the full-suite result of 6,517 passed and 4 skipped is the implementer-reported check, not a separate full-suite rerun by the reviewer.

**Implemented (descriptive only).**
- Records: `core/records/masking_measurement.py`.
- Preparation and manifest: `adapters/dsp_analysis/masking_preparation.py`.
- Analyzer: `adapters/dsp_analysis/directional_analysis.py`, identity `autonomous_mixing.p1_directional_analyzer@1`.
- Router: `adapters/dsp_analysis/analysis_router.py`.
- The qualified IDs of section 5 (`p1.support_cells@1` ... `p1.mean_positive_margin_db@1`) always return `Unavailable` with an uncalibrated-policy reason.
- Descriptive summaries over geometric support U use distinct IDs `p1.raw.*@1` with denominator `|U|`: geometric support cells, signed-margin median, positive-margin cells and fraction, mean positive margin, target-silent cells, masker-silent cells, quiet-floor threshold cells and non-finite margin cells.

**Evidence.**
- **Q:** `Q[b]` equals probe B's `thm_quiet` exactly on every call of the six fixture inputs checked; `test_directional_analysis.py` pins three of them.
- **Quiet-floor flags:** the per-cell flag `T == Q` equals probe B's `thm == thm_quiet` on the captured grids.
- **Capture replay:** probe B `thm`/`en` fed through the production cell selection and margin kernel agree with independent per-cell `10 * (log10 T - log10 E)` to below 1e-9 dB. This is an arithmetic check, not a front-end claim.
- **Full chain:** `capture/compare_band_margins.py`, unchanged, reports a largest absolute margin difference of 5.552e-03 dB, with no sign change, on the tone pairs and exact-silence controls. This is a finite-corpus observation, not a tolerance or a `delta_M`.

**Reviewed descriptive-slice decisions and precisions.**
1. **Preparation produces U only.**
   - Section 4 says preparation "verifies and measures" the initial state and derives S0. With no approved policy, R1 verifies the files (hash, profile) and computes geometry only. No DSP runs during preparation and no S0 exists.
   - The manifest records the uncalibrated status, the support kind, the verified baseline hashes, the geometry, the explicit cell list and the support digests.
   - Raw model values are produced per analysis call, as evidence of each state's current files.
2. **Integral interval boundaries.** A boundary is accepted only when it equals the declared frame time `timeline_start + i / rate` for some integer `i` in `[0, N]`, using the same float expression as the level analyzer's frame selection. Any other boundary is rejected, never rounded.
3. **Known alignment provenance.** Provenance is "known" when its method identity is in the preparer's configured set of documented methods; any other method is unavailable. A definition's `alignment` must equal the prepared provenance method.
4. **Quiet-floor comparison.** Per cell, the comparison is exact equality `T_A[k,b] == Q[b]`. `T >= Q` held on every fixture cell; this is not proven for arbitrary input.
5. **Evidence linkage.**
   - A core `Observation` has no evidence reference. Each evidence file therefore lists the observation IDs it backs, and the analyzer instance maps observation IDs to evidence digests in memory.
   - Durable linkage for restart is left to R5.
6. **Silent targets.** Raw positive margins with an exactly silent target are counted under `p1.raw.positive_margin_cells` and reported separately in `p1.raw.target_silent_cells`. They are not a diagnosis.

**Review fixes (2026-11-20, Codex P2 findings).**
- **Specification identity:** it now carries the prepared-plan digest. `bound_specification(name, version, digest)` returns `version+prepared.sha256.<digest>`, and `MaskingMeasurementPlan` refuses any other specification. This implements section 4, item 3.
- **Router:** validates each subresponse against its own subrequest before merging.
- **Preparation:** enforces cancellation and the time limit at completion as well as before each read, and writes no manifest when either check fails.
- **Manifest size:** preparation encodes the manifest once and refuses one longer than `MAX_MANIFEST_BYTES`, the bound the loader enforces, so every successful preparation reloads under the same limit (2026-11-22). Inclusive-boundary regressions pin this.

**Open questions.**
- Manifest and evidence size grow with clip length: about 63 numbers per call, plus 20 cells per call in the manifest. They are bounded by explicit byte limits, but no limit has been sized for full songs.
- Whether evidence should also store the masker's `en` and the target's `thm` for later qualification predicates is open.
