# HT01 descriptive P1 measurements

Recorded 2026-11-22, after owner authorization. **The existing exact-dual-mono path successfully measures HT01 Kick/Bass without creating audio copies. Whole-clip raw summaries are strongly affected by silent targets and quiet-floor cells; they are not ready-made diagnosis or acceptance criteria.** This is a private development analysis, not an agent trial, calibrated detector, perceptual evaluation or correction run.

## Scope fixed before execution

The five audited conditions are selected reference v002, corrected bass-up v002, kick-up v002, ducking-off extension v001, and reference repeat. Only their ten Kick/Bass WAVs are analyzed. Original selections and SHA-256 values come from the frozen D1 inventory (`783adb405ae2bbf97b05648bcfd410cf000e7166877699e7b97b462f256b1b3e`). Hat/Master remain genuine stereo and are not P1 inputs. No new render, copied WAV or mono fold-down.

The run uses existing `MaskingPreparer`, `DirectionalMaskingAnalyzer`, `AnalysisRouter`, production system clock/UUID identities and the reviewed kernels, with explicit prepared profile `p1.dist10_mono_pcm16_44100@2`. Full verified files remain 44,100 Hz stereo PCM_16, 1,365,677 frames. Both decoded channels must match exactly before one unchanged count sequence enters DSP. Original stereo metadata and hashes remain in raw evidence. The analysis did not alter the implementation.

Both directions use the whole file-relative interval [0, 1,365,677/44,100) seconds and bands 1–20. Band 0 is excluded by the P1 contract, not tuned to these results. All 2,371 calls run from call 0 with history intact; only complete-window eligible calls 2–2370 enter geometric support U: **2,369 calls × 20 bands = 47,380 cells per direction/condition**. No bars-based selection, activity cutoff, deadband, qualified support S0 or threshold was chosen. Whole-clip selection avoids adopting the earlier suggested bars 5–16 region without approval.

Timeline zero is explicitly file-relative. Common selection is producer-declared, with equal frame counts and prior technical alignment evidence; physical origin/alignment is not authenticated by this analysis. The method identifier for that declaration denotes provenance, not a measured alignment result.

## Raw results

Positive means the model threshold exceeds target energy in that cell. It does **not** mean audible masking. The positive-part mean is sum(max(margin, 0))/47,380, not the mean restricted to positive cells. All numbers below include geometric support's silent-target and quiet-floor cells exactly as the raw metric defines.

| Condition | Bass → Kick median dB | Positive cells | Positive-part mean dB | Kick → Bass median dB | Positive cells | Positive-part mean dB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| reference | 12.552725 | 31,390 | 22.580468 | -6.793607 | 22,366 | 14.462754 |
| bass_up | 12.552725 | 31,998 | 23.721998 | -9.157341 | 21,998 | 14.311355 |
| kick_up | 12.552725 | 30,896 | 22.275703 | -4.467905 | 22,819 | 14.955832 |
| ducking_off | 12.552725 | 31,574 | 22.638118 | -7.391919 | 22,192 | 14.393841 |
| reference_repeat | 12.552725 | 31,390 | 22.580468 | -6.793607 | 22,366 | 14.462754 |

**Why these are not a mixing score:**

- Reference Bass → Kick has 22,640 target-silent cells out of 47,380 (47.8%). All 22,640 are positive-margin cells, accounting for 72.1% of its 31,390 positives. Another 22,610 cells have masker threshold exactly at the model quiet floor; these categories overlap.
- Reference Kick → Bass has 19,620 target-silent cells (41.4%). They account for 87.7% of its 22,366 positives. Its quiet-floor count is 25,699 cells (54.2%).
- Bass → Kick's median remains 12.552725 dB in every condition despite gain/processing changes. That whole-clip statistic alone cannot distinguish the conditions or establish improvement.
- Bass-up raises the Bass → Kick raw positive-part mean and reduces the opposite direction's value; kick-up shows the converse. Ducking-off differs modestly in these aggregates. These are descriptive model responses, not validation of the sign or size of a correct action, audibility, or the dynamic-EQ setting.
- Full-clip exact-zero window flags vary slightly between exports because low count values and gain/export rounding can change whether a window is exactly zero. They are not a calibrated activity detector. All arithmetic margins in U are finite because the reviewed model includes spectral/energy floors; finite does not imply informative.

The private independent-check file includes the silence/quiet-floor cross-counts for every condition. No post-hoc subset is promoted to qualified support or fitted to reference preference. R2A remains INCOMPLETE and positive diagnosis/R2B is not activated.

## Reproduction and checks

Private output directory: `C:/uni/Bachelor-Thesis/test/ht01-p1-descriptive/`.

- `measurement-settings.json` fixes scope, resource limits, input-profile identity, inventory hash and measurement-script hash before computation.
- `plans/` contains five verified content-addressed prepared manifests; `plan-1.json` through `plan-5.json` record their digests. A rerun reuses and re-verifies these manifests rather than silently regenerating them.
- `run-1/` and `run-2/` hold ten content-addressed raw-evidence files each. Every direction's summary names its evidence SHA-256; the files include both source hashes, source/analysis channel counts, fixed mapping, complete call grids, values and flags, support cells and associated observation IDs.
- `run-1-summary.json`, `run-2-summary.json`, `verification.json` and `independent-checks.json` record results and verification. `provenance.json` records the actual uncommitted source-file hashes, Git HEAD and hashes of scripts/settings/summary evidence. These private artifacts are not an agent request or runtime reference bundle.

Commands, from the repository:

```powershell
uv run --locked --extra agent python C:/uni/Bachelor-Thesis/test/ht01-p1-descriptive/measure.py run-1
uv run --locked --extra agent python C:/uni/Bachelor-Thesis/test/ht01-p1-descriptive/measure.py run-2
uv run --locked --extra agent python C:/uni/Bachelor-Thesis/test/ht01-p1-descriptive/verify_evidence.py
```

Both executed runs agree exactly in numerical grids, scalars, flags, support and semantic evidence content. Actual creation times and fresh UUID observation IDs differ, so raw evidence files are **not claimed byte-identical**. The second run's content comparison excludes only those two volatile fields; all other evidence fields agree. Summary/evidence filenames also differ accordingly. The script refuses overwriting different existing run summaries; new reruns should preserve prior evidence in a fresh output set rather than overwrite it.

The reference repeat's complete numerical grid and raw summaries equal the reference exactly in both directions. This supports this pair of exports only, not universal FL rendering determinism. Independent scalar/geometry checks recompute every stored margin with direct scalar arithmetic and check all raw count/median/positive-part-mean summaries and flags. Largest margin disagreement is 2.842171e-14 dB; the 1e-10 arithmetic comparison allowance is a verification check, **not** a model deadband or end-to-end tolerance.

All 63 original-file hashes from the D1 audit match before and after each measurement run. No source/project file was changed, no provider request or live FL Studio operation occurred, and no new audio was written. No software suite rerun is claimed for this data-only task; the preceding implementation's tests are recorded separately.

## Next bounded step

Review an explicit qualification-policy proposal using this development evidence and separate numerical controls: silence/activity handling, quiet-floor exclusion, low-energy/quantization sensitivity, a justified numerical deadband and evidence sufficiency. Do not select values by making the preferred reference win or treating its difference as the runtime oracle. Keep the reference, condition labels and informal judgments private. R2B positive diagnosis and R5 agent-loop composition remain separate review gates. The thesis can assess evidence use, tools, bounded decisions and feedback while keeping musical/perceptual claims limited.
