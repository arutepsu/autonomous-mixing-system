# P1 minimum qualification proposal

**Status:** Structural design approved by the owner, 2026-11-22. Numerical values and activation remain unapproved. Documentation only. No policy values approved, no qualified support produced, no production behavior changed, and no further analysis or experiment was run at proposal creation; the later authorized control results are linked below.

**Purpose:** Give the agent a defensible experimental model-risk signal while keeping the thesis centered on evidence, tools, bounded actions, feedback and verification. This is not a proposal for a general perceptual masking detector or a larger mixing product.

**Basis:** [accepted P1 method](../superpowers/specs/2026-11-20-p1-diagnosis-method.md), sections 3–8; [HT01 descriptive results](ht01-p1-descriptive-measurements.md); [optional extended registration](../superpowers/specs/2026-11-22-p1-calibration-registration.md). This smaller proposal does not adopt that registration's project/listener counts, parameter grids or numerical values.

## Recommended approach and alternatives

Keep the existing method and components. First separate cells that demonstrably cannot support the intended interpretation; then establish a small empirically screened numerical domain with controlled signals. Only afterward approve a frozen, explicitly experimental qualification policy and implement it through the existing preparation/analyzer/diagnosis boundaries.

Tuning a gate until the preferred HT01 reference wins is rejected: the reference is private development evidence, not a runtime objective. Enabling diagnosis after deleting silence alone is also rejected: nonzero but quantization-sensitive energy and tiny numerical elevation above quiet remain unresolved. A large listener panel or broad parameter search is not required by this proposal; neither supplies the missing numerical checks automatically.

## 1. Structural rules proposed now

These follow the accepted method. Approving their design does not make qualified metrics available before the numerical work below.

| Initial-state condition | Proposed treatment | Interpretation |
| --- | --- | --- |
| Cell outside declared complete-window geometry or bands 1–20 | Outside U | Preserve all full-clip/band evidence; no favorable interval/band search. Band 0 remains unsupported for diagnosis. |
| Either source window has exactly zero original PCM counts | Exclude from initial qualified support | A model floor is not evidence of an active source. Exact nonzero is a necessary check, not a useful loudness/activity threshold. |
| Masker threshold T is equal to or below quiet floor Q | Exclude from initial qualified support | No demonstrated model elevation above quiet. Do not subtract Q from T or change the margin formula. |
| Required evidence is absent, invalid or non-finite | Unavailable, with the reason retained | Do not replace with zero or report a clean condition. Where a missing prerequisite prevents assessing qualification, fail preparation rather than quietly construct a smaller qualified set. |
| Nonzero source outside the tested numerical/quantization domain | Unqualified, with explicit domain limitation | Nonzero alone does not make a reliable cell. The applicable domain is not established yet. |

Record overlapping reasons separately. A cell may be silent and quiet-floor-limited; exclusion counts must not be added as though mutually exclusive. Empty support means unavailable, not zero risk, a successful mix, or an abstention that achieved its target.

The T≤Q check is only the structural minimum. A positive floating-point difference is not sufficient: a later approved quiet-elevation allowance must also be met. No allowance is selected here.

## 2. Numerical choices still unset

| Parameter family | What it would govern | Evidence required before choosing it |
| --- | --- | --- |
| Target/masker band-energy regime | Which nonzero cells are sufficiently far from quantization sensitivity | Actual PCM16 re-quantization/perturbation controls across phases, levels and decays. A theoretical rounding-noise reference is a diagnostic, not a measured noise floor. |
| Quiet-elevation allowance | How far T must exceed Q before qualification | Reference/Python threshold agreement and rounding/tie controls in the applicable domain. Separate from margin deadband. |
| Margin deadband δM | Robust exceedance uses M>δM; near-zero signs remain indeterminate | Complete-chain numerical residuals, quantization sensitivity, a declared conservative allowance and an explicit tested domain. |
| Evidence sufficiency | Minimum support, fraction of robust exceedance and persistence | A small predeclared operational-policy rationale and documented development checks; label these experimental choices rather than published psychoacoustic thresholds. |

The prior 0.005552 dB fixture discrepancy is not δM. The latest 2.842171e−14 dB arithmetic agreement checks calculation on stored values, not the whole front end, quantization stability or perception. Neither value authorizes a threshold. Earlier 10/20/40 dB descriptive noise groups and the optional registration's numerical grids are not adopted.

No universal numeric error bound can be inferred from finitely many controls. A policy must state where its empirical envelope applies and refuse extrapolation. If suitable stability cannot be demonstrated, qualified P1 remains unavailable; do not invent a fallback value.

## 3. Freeze support on the actual initial input

1. Choose permitted current sources, pair, region and time interval prospectively. The input may be a development condition, but its private reference, injected gain and preferred result are not preparation inputs.
2. Verify full current files, run complete histories and evaluate the approved predicates on that initial state only.
3. Persist exact qualified cell IDs S0, source hashes, geometric support, policy/version, parameter values and evidence identity before a correction is proposed.
4. Assess every later candidate against the same S0 and denominator. A newly convenient subset cannot make the edit pass.

If a later target becomes exactly silent or leaves the tested energy regime, the fixed-support comparison is unavailable. A nonzero masker leaving its tested regime also makes comparison unavailable. A masker becoming exactly silent or quiet-floor-limited may represent an actual reduction: retain its finite margin on S0 only under the separately verified quiet-floor control. Never delete those cells to improve a score. Current flags remain diagnostic evidence, not permission to rebuild S0.

For later recovery/restart, missing or changed support evidence refuses continuation; it is never regenerated from a candidate. Independently prepared conditions with different S0 are not interchangeable comparisons even if they contain the same number of cells.

## 4. Small next experiment, separate from activation

The next reviewable deliverable is a short numerical-control recipe, not positive diagnosis code. It should fix deterministic signals/seed and hashes, exact int16 generation/rounding, levels/phases/decays, both directions, bands, histories and selected boundary cases **before running them**. Use existing fixture/reference capture and production kernels; no new adapter, experiment framework or mandatory music corpus.

Cover these failure modes with the smallest controls that distinguish them:

- Exact silence and quiet-floor ties; ensure finite model floors do not count as active audio.
- Sustained signals plus low-energy decays, with declared bounded PCM16 perturbations/re-quantization; examine target and masker separately. Exact-dual-mono selection adds no rounding, unlike averaging.
- Cases near M=0 and T=Q, including startup/history and complete-window boundaries; compare independent captured T/E/M with the production chain where available.
- A silent/quiet-floor masker after an initial qualified state, and a muted target; establish why the former can remain assessable and the latter cannot.

Report every registered case, not only favorable ones. If no independent capture exists for a new control, label internal consistency separately from reference reproduction. Do not silently expand the reference implementation or use agreement between the same function called twice as independent validation. Selecting numerical gates and sufficient evidence remains a separate review after results; this proposal authorizes neither generating those controls nor running the optional registered study.

HT01 can subsequently show how the frozen rules behave on music, with all related conditions remaining development-only. No further FL render or hired listener is needed for numerical checks. Later listening is supplementary unless the final research claim requires stronger perceptual evidence.

## 5. Small implementation after policy approval

Reuse existing ownership:

- `wav_input` and numerical kernels retain their current responsibilities and bytes/formulas.
- Preparation verifies and freezes S0; the analyzer computes fixed-support scalars/flags and persists evidence. Do not introduce a second qualification pipeline.
- Core diagnosis consumes exactly bound scalars and the frozen policy; it neither reads audio nor estimates new thresholds. The AI receives producer intent, qualified evidence/limitations and allowed actions; it does not decide measurement validity.

One concrete evidence gap must be covered when this is implemented: low-energy screening needs **masker band energy E_A as well as target energy E_B**. The existing front end already returns both sources' energy/threshold arrays, but one direction's saved R1 evidence currently stores only T_A and E_B. Do not infer E_A from T_A, require a separately requested reverse job, or fit energy from a margin. Retain the needed existing front-end value with explicit provenance in the qualification evidence; review its minimal versioned format at implementation planning. No new DSP algorithm is required.

Test exact silence, quiet-floor ties, near-boundary numerical cases, missing energy evidence, empty S0, fixed denominator/cell identity, target muting, a quiet-floor masker, unchanged support across restart and source/policy mismatches. Keep descriptive R1 IDs and legacy evidence compatible. Do not label a partial hard-exclusion subset as S0 or emit qualified-named metrics early.

## Decision and stopping point

Recommended decision: approve the structural design and commission the small numerical-control recipe, while leaving all numerical values and activation unapproved. Do not implement R2B or wire R5 from this document. R2A stays INCOMPLETE with zero candidates; raw measurements remain available. Candidate generation, operational acceptance guards and the eventual thesis evaluation are distinct decisions.


## Follow-up planning

The owner approved the structure and preparation of a small numerical-control recipe. The [proposed control plan](../superpowers/plans/2026-11-22-p1-small-numerical-controls.md) now fixes a bounded engineering check for review. Its recipe and tests-first implementation have since been approved; the bounded checker is implemented, uncommitted for review. The registered run was subsequently separately authorized and completed twice; its [results](p1-small-numerical-controls-results.md) establish no qualification values or diagnosis status change.
