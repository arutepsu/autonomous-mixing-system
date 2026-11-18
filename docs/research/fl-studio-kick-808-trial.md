# Kick/808 gain trial in FL Studio: before/after analysis

**Manual feasibility trial**, 2026-11-17, owner-authorized under ADR-009 (see `fl-studio-gain-trial.md`). This is a technical comparison of two FL Studio exports, not a mixing result. Whether the mix sounds better is left to a separate listening comparison. The production WAV input profile (mono 44,100 Hz PCM_16, ADR-029) is unchanged; the mono files below are derived analysis inputs only.

## Inputs

There are six split exports in `C:/uni/Bachelor-Thesis/test/trap-baseline`, made by the owner in FL Studio 2026 from `trap-baseline.flp` and `trap-candidate.flp`. They were only read: their SHA-256 values, and those of the `.flp` files and backups, were unchanged after every run. Every export is WAV, PCM_16, 44,100 Hz, **stereo**, 302,400 frames (6.857 s).

| Export | SHA-256 |
| --- | --- |
| `trap-baseline_Kick.wav` | `e445daebc3086e69c6a98f7512b63e492adbe3b4ee600d91384b1209380a135a` |
| `trap-baseline_Bass.wav` (808) | `74c82cd4fc0ead049aa1b7afdc7e1fb5ac32098a8212210617f09a4e82d05d19` |
| `trap-baseline_Master.wav` | `c3badaf048dfa7f4cea3ff4b5c9da9e39775ea93fde6baf1cc97ad6d666d354d` |
| `trap-candidate_Kick.wav` | `915f125182ed42d6b9f91b49c6f030ee9858324f8dbf0911e4cd5deea8e0b1ef` |
| `trap-candidate_Bass.wav` (808) | `36e93aab981ff2d1a2bf4c1e4398894b4619e1a92036ec48f15ec21a08ed5e14` |
| `trap-candidate_Master.wav` | `13982f1c499450d0d455336e9e12774695cbd7b53eb9c01b94ff4de0c4ed22db` |

**What changed** (`check_exports.py` comparison of the decoded samples):
- **Kick:** the change was applied here, with a least-squares gain of **−2.974 dB** (residual energy 2.0e−9 of the candidate's) and lag 0.
- **808:** the audio is **bit-identical**, with lag 0. The two files differ only in the RIFF `LIST/INFO/INAM` title ("trap-baseline (Bass)" against "trap-candidate (Bass)"); their data chunks are byte-identical.
- **Master:** changed, with lag 0.
- **Alignment:** all six files share one length, and the stems show no offset at sample resolution (±4,410 samples searched). This report does not establish how the cut was made in FL Studio (fader, script or other).

The export format does **not** meet the production masking profile (stereo, not mono). The originals are kept unchanged, and this mismatch is the input for the next design step.

## Channels and mono rule

Left and right were compared before any fold-down. The "mean energy" figure is the energy of the arithmetic L/R mean relative to the mean channel energy; 0 dB means nothing cancels.

| Stem | Identical L/R | Max \|L − R\| (counts) | Correlation | Mean energy vs channel energy | Side vs mid energy |
| --- | --- | --- | --- | --- | --- |
| Kick (baseline) | no | 1252 | 0.99982 | −0.0004 dB | −40.6 dB |
| Kick (candidate) | no | 890 | 0.99982 | −0.0004 dB | −40.6 dB |
| 808 (both) | no | 2 | 1.0000000 | −0.000000 dB | −88.4 dB |

**No substantial cancellation**: averaging loses less than 0.001 dB of energy in either stem.

**Derived mono analysis input** (trial rule, applied to all four stem exports): `m = round_half_even((L + R) / 2)`, computed exactly from the int16 counts (`L + R` in int64; halves are exact in float64; NumPy rounds half to even) and cast to int16. The result always lies in [−32768, 32767]. The derived files are mono PCM_16 WAVs in `C:/uni/Bachelor-Thesis/test/trap-baseline-analysis`:

| File | SHA-256 |
| --- | --- |
| baseline kick | `696b1ba5b2a21659fea76d869c214aae7d2db4f23950ba2c952266d803749d3f` |
| candidate kick | `99e9fe12c2338c7e66f9a662b74274bd1705d3fbf89b60b9792a709f48424fba` |
| baseline and candidate 808 | `b63084fad981ecd840e54cbc34da4552b5f333b72403a1f42ef572879e488542` (identical) |

The masters stay stereo.

## Analysis settings

- Front end: `dist10_clip_to_bands` (profile v1, 44,100 Hz), on each derived mono stem, with history from call 0.
- Margins: `directional_masking_margin_db`, with `T` = masker `thm` and `E` = target `en`.
- Frames: the **523 calls eligible in all four clips** (calls 2–524; k ≥ 2 and 576k + 256 ≤ N), all 21 long bands, the same frames and bands before and after.
- Silence: target windows with no nonzero sample (`nonzero_samples == 0`) are flagged and excluded from the change statistics.
- Band frequencies are **nominal**: the lowest bin of partition `bu` to the highest bin of partition `bo`, counting bins below 465 only (bin k is k × 44,100/1024 Hz). dist10 also assigns bins 465–512 (about 20.0–22.05 kHz, Nyquist included) to partition 0, which is band 0's lower endpoint with weight 1.0. Band 0 therefore also holds energy far outside its displayed nominal 0–172 Hz. Every frequency range below is nominal.
- Rounding-noise reference: int16 rounding noise gives about (1/12) × Σw² ≈ 32 units of FFT energy per bin. It is taken over **every bin 0–512, Nyquist included**, because the reproduced dist10 partition mapping accumulates bin 512 into partition 0, although the FFT energy floor excludes it. It is mapped to bands like any energy and serves as a descriptive reference, not a filter. Including Nyquist (corrected after review) raises only band 0's reference, from 1,633.8 to 1,665.8 (+0.08 dB); no noise-group count or summary changed.
- Output: `trial-analysis.json`. Two repeated runs were byte-identical.

## Results

| Direction | Cells compared (silent target windows excluded) | Median change | Mean | Min / max | Increased / decreased / unchanged | Cells with T > E, before → after |
| --- | --- | --- | --- | --- | --- | --- |
| kick → 808 (kick threshold over 808 energy) | 6,657 (206 silent 808 windows) | 0.00 dB | −0.19 dB | −6.13 / +7.78 dB | 166 / 643 / 5,848 | 125 → 98 (27 to ≤ 0, none to > 0) |
| 808 → kick (808 threshold over kick energy) | 1,932 (431 silent kick windows) | +2.96 dB | +1.98 dB | −7.78 / +6.23 dB | 1,641 / 291 / 0 | 543 → 550 (10 to > 0, 3 to ≤ 0) |

**Expected effect where the signals are well above rounding noise.** The changes match the −2.974 dB kick cut where the relevant band energy is at least 40 dB above the rounding-noise reference:
- **808 → kick:** the kick energy `E` falls, so the margins rise. Over 675 cells the change is +2.87 … +3.02 dB, median +2.974 dB.
- **kick → 808:** the kick threshold `T` falls, so the margins fall. Over 320 cells the change is −3.22 … 0.00 dB, median −2.973 dB.

Most kick → 808 cells are unchanged because the kick's threshold there is threshold in quiet. The kick is absent from most frames and bands, so its masking threshold does not move.

**Where the larger changes occur.** The kick plays in short hits of about 11–12 calls (for example calls 0–11, 99–109 and 131–142) separated by exact silence.
- **Time:** every change larger than ±3.5 dB, increases and decreases alike, falls in the **late calls of a kick hit, its decay**. There are 36 such cells for kick → 808 and 110 for 808 → kick, for example calls 6–11, 105–109, 137–142, 269–273 and 498–503.
- **Frequency:** bands 6–13 (nominal 0.9–5.6 kHz) for kick → 808, and bands 3–20 for 808 → kick.
- **Energy:** in these cells the kick's band energy is at most 25 dB above the rounding-noise reference, with a median of about 2 dB. 69% (kick → 808) and 77% (808 → kick) are within 10 dB.
- **Example:** kick → 808 at call 7, band 9 (nominal 1.68–2.11 kHz) changes by +7.78 dB with the kick 0.3 dB below the reference.
- **Mechanism:** in such bands the candidate kick is not a scaled copy of the baseline; at call 7 its partition energies in band 9 rise by up to +11 dB despite the −3 dB cut. The tonality, SNR and pre-echo stages then turn small low-level differences into threshold swings.

These outliers are **consistent with sensitivity to quantization in low-energy bands**, not with the applied gain, and should not be read as masking changes. The trial contains two roundings: FL Studio's PCM_16 export of each condition, and the derived mono conversion (half-even rounding of the L/R mean). It cannot isolate their individual contributions.

**By band** (mean change over the compared cells):
- 808 → kick: about +3.0 dB in bands 0–2 (nominal 0–474 Hz), +2.5 to +2.7 dB in bands 3–5, and +1.0 to +2.0 dB above 0.9 kHz, where more kick cells are near rounding noise.
- kick → 808: −0.35 dB in bands 0–3, about −0.1 to −0.2 dB above, diluted by the unchanged threshold-in-quiet cells.

**Crossings of 0 dB:**
- **kick → 808:** 27 cells, all in the early calls of kick hits (calls 2–4, 134–135, 265–266, 396–398), went from the kick threshold exceeding the 808 energy by 0.1–2.8 dB to not exceeding it. The kick is 17–94 dB above rounding noise in all of them, so they are consistent with the cut.
- **808 → kick:** 13 cells crossed. The 10 that became positive have the kick within 28 dB of rounding noise in bands 12–19. The 3 that became non-positive are all in band 20 (nominal 13.4–19.2 kHz), with the kick only 2–3 dB above rounding noise.

## Master levels (stereo)

| | Sample peak | RMS (pooled channels) |
| --- | --- | --- |
| Baseline | −1.187 dBFS | −15.250 dBFS |
| Candidate | −2.436 dBFS | −15.870 dBFS |
| Change | **−1.249 dB** | **−0.620 dB** |

## Reproduce

From the repository root:

```powershell
uv run python scripts/fl_studio/check_exports.py --baseline 'C:/uni/Bachelor-Thesis/test/trap-baseline' --candidate 'C:/uni/Bachelor-Thesis/test/trap-baseline' --role kick=trap-baseline_Kick.wav:trap-candidate_Kick.wav --role 808=trap-baseline_Bass.wav:trap-candidate_Bass.wav --role master=trap-baseline_Master.wav:trap-candidate_Master.wav --changed kick --out 'C:/uni/Bachelor-Thesis/test/trap-baseline-analysis/export-check.json'
```

That command reports the kick gain (−2.974 dB), the 808 bit identity, lag 0, the common length and the profile mismatch ("2 channels") for every file. The analysis:

```powershell
uv run python scripts/fl_studio/analyze_trial.py --exports 'C:/uni/Bachelor-Thesis/test/trap-baseline' --out 'C:/uni/Bachelor-Thesis/test/trap-baseline-analysis'
```

```powershell
uv run pytest -q tests/unit/test_fl_trial_analysis.py tests/unit/test_fl_export_check.py
```

## Limits

- One manual trial, one cut, one short loop (6.9 s); the derived mono files are analysis inputs, not production inputs.
- The mono rule, the rounding-noise reference and its 10/20/40 dB groups are trial choices for description, not validated thresholds.
- The export's PCM_16 rounding and the mono conversion's rounding both act on low-energy bands; this trial cannot separate them (a float or 24-bit export, or a mono export, would).
- Frequency ranges are nominal; band 0 also contains bins 465–512.
- The masking margins are the dist10 model's numbers, not perceptual masking. No conclusion about sound quality is drawn.
- `check_exports.py` now accepts `role=baseline.wav:candidate.wav` for FL Studio's project-name prefixes (added for this trial; 13 tests).
