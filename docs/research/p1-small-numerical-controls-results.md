# Small P1 numerical controls — results

Recorded 2026-11-22 after owner authorization of implementation and execution. **Both registered runs completed and their numerical evidence matches exactly. Existing independent captures reproduce closely, while deterministic sub-count perturbations produce substantial weak-band sensitivity. These results justify retaining a qualification gate; they do not establish its numerical values.** No policy, qualified support S0, positive diagnosis or agent loop was activated.

## Registered scope and execution

Recipe: [approved small-control plan](../superpowers/plans/2026-11-22-p1-small-numerical-controls.md); governing [qualification structure](p1-minimum-qualification-proposal.md). All new samples are short, generated numerical controls, not a musical evaluation dataset or evidence of agent performance. No HT01/reference music, provider or FL Studio operation participated.

The checker executed exactly **198 new direction-cases per run**: 120 sustained/decay representation cases (including 24 nominal cases), 10 zero/tail-zero cases and 68 bounded boundary-search cases. Existing independent-reference comparisons are separate: nine sources and eight directional pairs/controls. Each full-length generated input has 6,912 counts at 44,100 Hz, 12 processed calls, and 10 eligible calls × 20 supported bands = 200 geometric cells. All calls and 21 bands are retained; only eligible bands 1–20 enter the sensitivity summaries below. Band 0 is not used for diagnosis.

Private evidence: `C:/uni/Bachelor-Thesis/test/p1-small-numerical-controls/`.

- Frozen `control-inputs.json`: SHA-256 `ee4040658de096e0f5661c3ad58a543d34a9e5544c6c1179b6407b06041c0761`. Its fingerprint records the actual uncommitted checker/production/helper/capture sources and environment, rather than relying on Git HEAD alone.
- Exact initial generated raw storage: 580,608 bytes. Boundary inputs and pending intent were additionally persisted before each attempted calculation.
- Each completed run contains **338 hashed numerical files**, identical byte-for-byte across runs. Completion markers differ in elapsed time and repeat status and are not claimed identical.
- Run 1 elapsed **4.657 s**, run 2 **16.109 s**, versus the registered 300-second bound. Recorded output size is **8,993,975 bytes per run**, below 128 MiB. These are process timings/output sizes, not a peak-memory benchmark or performance comparison.
- `result-summary.json`, `summarize_results.py` and `verification.json` retain derived tables, their calculation, source hashes and verification. Every stored numerical file in both runs was re-hashed after completion; all match the completion records. The checker also reverified the frozen inputs and source/capture fingerprint before completing each run.

## 1. Independent reference reproduction

The unchanged fixture verifier and comparison command both returned success. Metadata agrees across all **92 calls of nine inputs**. Per-source E/T residual evidence contains 1,932 call-band rows, including band 0 and startup/ineligible rows; **1,460** rows are eligible bands 1–20. These overlapping windows/bands are not independent statistical observations.

| Quantity | Largest recorded error, eligible bands 1–20 | Interpretation |
| --- | ---: | --- |
| Band energy E, relative | 0.001279350428 (about 0.128%) | Empirical worst case on the fixed captures; relative error may be prominent in weak bands. |
| Band threshold T, relative | 0.001051880645 (about 0.105%) | Empirical worst case on the fixed captures, not a universal error envelope. |
| Directional M, absolute | 0.005552104558 dB | Maximum across the eight recorded directional pair/control comparisons; no sign changes. |

Directional margin residuals retain all 2,016 call-band rows; 1,600 are eligible bands 1–20. The largest margin error occurs at call 9, band 12, with `target_2k_a1024` as masker and `masker_1k_a8192` as target: reference 46.341339139 dB, Python 46.335787035 dB. Startup/ineligible directional rows are separately retained; their largest absolute margin error is 0.000104324 dB. This new summary reproduces the earlier 0.005552 dB observation rather than expanding its validity to arbitrary signals.

Energy/threshold relative and log errors and margin **dB differences** remain separate; no logarithm of an already-dB margin is used. Zero-reference errors have explicit unavailable relative/log values. No captured value or fixture was regenerated or changed.

**Limit:** the generated perturbation/decay/boundary controls have no new independent dist10 captures. Their repeatability is internal engineering evidence, not additional independent backend accuracy. In particular, the existing fixture maximum is not automatically an error allowance for the new near-zero cases.

## 2. Deterministic PCM16 perturbation sensitivity

The 12 base pairs each have nominal, A-plus, A-minus, B-plus and B-minus representations. The offsets are ±0.5 count applied to the real waveform before half-even rounding. Changed samples differ from nominal by at most one PCM16 count; the other source is byte-identical. Both directions are analyzed.

Below, nominal rows are excluded from delta counts: **96 perturbed direction-cases × 200 cells = 19,200 comparisons**. These are repeated, dependent control comparisons, not a sample size for music or audibility. Larger sustained target amplitude is a generation setting, not a per-band energy guarantee.

| Control family | Perturbed direction-cases | Cell comparisons | Largest absolute change in M, dB | Sign changes |
| --- | ---: | ---: | ---: | ---: |
| Sustained target amplitude 1 | 16 | 3,200 | 14.090121 | 34 |
| Sustained target amplitude 4 | 16 | 3,200 | 12.525142 | 53 |
| Sustained target amplitude 16 | 16 | 3,200 | 18.997460 | 30 |
| Sustained target amplitude 256 | 16 | 3,200 | 17.220774 | 20 |
| Sustained target amplitude 1024 | 16 | 3,200 | 10.801268 | 16 |
| Decay tau 0.02 s | 8 | 1,600 | 9.780471 | 4 |
| Decay tau 0.1 s | 8 | 1,600 | 9.780471 | 10 |

Across all perturbations there are **167 sign changes**. The maximum change is **18.997459625 dB**, case `tone-16-0-B-plus`, direction B→A, call 3, band 4. The changed B source is the masker in this reverse direction. Its current band energy is **−0.733312 dB relative to the declared diagnostic rounding-noise energy**, while target-band energy is +55.135911 dB relative to that reference. The current margin is −65.119588108 dB, versus about −84.117047733 dB nominal: both are negative. Thus the largest magnitude change is not itself a zero-crossing or an actionable risk finding.

This is **sensitivity to changed input counts**, not a 19 dB numerical error of the Python backend. The perturbations are deterministic biased rounding controls, not independent white noise, a reproduction of FL dither, or a complete model of all export errors. The noise reference is theoretical; no measured noise-floor or psychoacoustic threshold is inferred. Even louder full-band tones can have weak individual bands, so a full-clip nonzero check or overall level cannot replace band-specific evidence.

No energy cutoff was fitted to these rows. Every registered case is retained, including unstable cells; no favorable high-energy subset was selected as qualified.

## 3. Silence, quiet floor and fixed diagnostic cells

The nominal zero-control pair has **200 structural diagnostic C0 cells in each direction**. This is deliberately not S0: it lacks a validated energy regime and numerical allowance. All zero/tail-zero variants retain those same 200 cell IDs, rather than recomputing a smaller denominator.

- Entirely muting the masker sets all 200 C0 cells to raw-window silence and the exact modeled quiet floor, while margins remain finite.
- Entirely muting the target sets all 200 target-silence flags, while floored target energies still produce finite margins. Finite M therefore cannot establish usable target evidence or successful correction.
- Tail zeroing at sample 3,456 yields 80 exactly silent cells (four eligible windows × 20 bands) on the zeroed source. When that source is the masker, those 80 cells are quiet-floor cells too. The observed counts are reported; no general settling duration is adopted.

These controls support preserving original-count silence flags and fixed comparison identity. They do not approve a complete qualification predicate or claim that muting an actual musical masker is desirable. Initial structural exclusions alone remain insufficient for low-energy stability.

## 4. Near-zero boundary controls

Both phases bracketed zero at the selected call 6 / band 6 and finished at adjacent integer amplitudes after 17 attempts each (two endpoints plus 15 midpoints). The band was selected by the registered maximum-energy/tie rule, not by musical outcomes. Every attempt and exact input hash is retained, with both directions; these are the 68 boundary direction-cases.

| B phase, radians | Lower amplitude | M at lower amplitude, dB | Upper amplitude | M at upper amplitude, dB | Nearest absolute M, dB |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0.000000000 | 4127 | +0.001505096 | 4128 | -0.000577645 | 0.000577645 |
| 1.570796327 | 4127 | +0.001686755 | 4128 | -0.000506329 | 0.000506329 |

No exact zero or fractional-count interpolation was forced. These cases show that a one-count amplitude step can cross the raw model boundary at this configuration. The nearest values, about 0.0005–0.0006 dB, are smaller than the old fixture maximum, but **that maximum is not independently verified for these new inputs**. This comparison motivates testing a deadband; it neither selects one nor turns either sign into a reliable diagnosis.

## Reproduce and verify

Commands actually executed, from the repository:

```powershell
uv run --locked --extra agent python scripts/research/check_p1_numerical_controls.py prepare --out C:/uni/Bachelor-Thesis/test/p1-small-numerical-controls
uv run --locked --extra agent python scripts/research/check_p1_numerical_controls.py run --out C:/uni/Bachelor-Thesis/test/p1-small-numerical-controls
uv run --locked --extra agent python scripts/research/check_p1_numerical_controls.py run --out C:/uni/Bachelor-Thesis/test/p1-small-numerical-controls
uv run --locked --extra agent python C:/uni/Bachelor-Thesis/test/p1-small-numerical-controls/summarize_results.py
```

The directory now holds both registered runs. `prepare` refuses existing output and `run` refuses a third run; preserve these records rather than overwriting them to rerun. A future separately authorized reproduction needs a fresh directory with the same frozen source/environment and exact input bytes. The private summary command only reads/rehashes evidence and refuses replacing a differing summary; it does not generate controls or select a policy.

A direct scalar recomputation from the stored source T/E confirms the worst sensitivity case's current margin. Numerical file hashes agree exactly between runs. Existing fixture/source hashes match the frozen fingerprint before and after; no musical media was read or modified. This data-only execution changes no product code, so no new full-suite result is claimed here. Implementation checks (34 focused cases and 6,921 passed / 4 skipped overall) are recorded separately.

## Conclusion and next bounded decision

Supported now: the checker executes reproducibly; existing reference agreement is reproduced; raw silence/quiet-floor flags and fixed cell identity behave as designed; low-energy input sensitivity and near-zero boundary cases are concrete.

**Not established:** a reliable general energy domain, a justified quiet-elevation allowance or margin deadband, evidence-sufficiency values, perceptual masking, a correct musical action, or an agent improvement.

Recommended next step is a small independent-reference capture plan for **already-generated** boundary and representative sensitive/decay inputs, preserving their hashes and all prior results. Such a follow-up can address the missing accuracy evidence without collecting another musical project or building another adapter. Select any subset and its rationale before capture; do not claim the selected cases independently validate all 198 controls. Then review a limited experimental energy/uncertainty policy. Do not set a large deadband from the 19 dB input sensitivity, and do not set a tiny one from the 0.005552 dB fixture maximum. R2A remains INCOMPLETE, and R2B/R5 remain gated.


## Follow-up status (2026-11-23)

The owner subsequently authorized a fixed independent capture subset. [Selected independent-reference results](p1-independent-reference-results.md) now cover the boundary endpoints and selected sustained/decay nominal/perturbed pairs. The new maximum observed supported margin error is 0.006586305084 dB with no sign disagreement in 4,000 selected cells. The approximately 19 dB input effect is reproduced by C. This extends only the selected coverage; the initial study's results and lack of qualification values remain unchanged.
