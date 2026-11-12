# MPEG psychoacoustic front end for directional masking: source trace and draft numerical contract

Status: draft for Codex review (Task 2 of the directional-masking-margin work), revision 2 (2026-11-11). Source tracing and reference evidence only: no production front end, analyzer integration or report change. It follows the pairwise-masking reproduction spec (Codex project folder, `2026-11-11-pairwise-masking-reproduction.md`, section "MPEG front end"). The existing kernel `adapters.dsp_analysis.masking.directional_masking_margin_db` (commit `4d620a0`) consumes the two arrays this front end must produce.

Revision 2 supersedes these statements of revision 1: the thesis is now read (§1), so the equation numbering is the thesis's and is no longer unresolved. The first profile is pinned to 44.1 kHz, mono, int16 (§2); revision 1 proposed 44.1 and 48 kHz and a float-input rule. The end-of-stream rule is replaced by the final-frame rule (§2.2). The short-block path is now instrumented by a separate probe (§3).

Labels: **[T]** defined by Ronan's thesis; **[S]** defined by the dist10 source, our chosen backend; **[R]** observed in the reference run; **[P]** project choice proposed for review; **[U]** unresolved.

## 1. Source trace

| Step | Checked (2026-11-11) | Result |
| --- | --- | --- |
| Ronan, D. M., *Intelligent Subgrouping of Multitrack Audio*, PhD thesis, Queen Mary University of London (QMRO record date 11/01/2019; C4DM lists it as a 2018 thesis) | QMRO `https://qmro.qmul.ac.uk/handle/123456789/55527` (found through the C4DM theses page; the earlier spec URL used the old `xmlui` path), file `RONAN_David_PhD_Final_110119.pdf`, 24,197,020 bytes, SHA-256 `216f5571db575024e9e30a7cd5e073722b8669a59260c83cb6d049f5b777b9f2`. Read Ch. 7, §§7.2.3–7.2.6, printed pp. 102–111 (PDF pp. 117–126); §§7.2.4.1–7.2.4.2 from the rendered pages 105–109, because pp. 106 and 108 carry a hidden text layer from a paper draft that corrupts text extraction | Primary source for the cross-adaptive calculation; see §1.1. The PDF is not stored in the repository. |
| Ronan, Ma, Mc Namara, Gunes, Reiss, *Automatic Minimisation of Masking in Multitrack Audio using Subgroups*, arXiv:1803.09960v1 (27 Mar 2018; v3 withdrawn 2021) | Read pp. 1–8 and 12–14 | Secondary. Its Eqs. (16)–(18) match thesis Eqs. 7.11–7.13; the threshold wording is the same. |
| Author code, `code.soundsoftware.ac.uk/projects/automatic-minimisation-of-masking-in-multitrack-audio-using-subgroups` | Connection refused (ECONNREFUSED), first attempt only | **[U]** Unknown whether Ronan used the ISO reference code, a port or own code. Ch. 7 does not say. |
| ISO/IEC 11172-3:1993 Annex D (psychoacoustic models, informative) | Not consulted: paywalled; an unofficial copy found by search was not used | **[U]** Normative wording not checked. |
| ISO MPEG Audio Software Simulation Group, *dist10* (1996), `lsf/encoder/l3psy.c` (`L3psycho_anal`), `subs.c` (`fft`), `tables/psy_data`, `encode.c`, `musicin.c` | Original archive `orig/dist10.tar.xz` from `github.com/joncampbell123/iso-dist10` at commit `ea3c5ee`, SHA-256 `79f8321b…f369`; read and run | **Our chosen backend [P]**, not Ronan's documented implementation. Reference implementation of MPEG-1 model 2 in its Layer III form; line references below are to this `l3psy.c`. |

### 1.1 What the thesis defines [T]

Thesis §7.2.4.1 (pp. 105–107) describes the MPEG psychoacoustic model (citing Brandenburg & Stoll 1994, [155]) in prose with Fig. 7.2. In order:
- Complex spectrum by a standard forward FFT.
- Tonality index from predictability [157].
- Spreading function `s_f` (Eq. 7.8), with `B(dz)` "found in" Bosi et al., *ISO/IEC MPEG-2 Advanced Audio Coding* [31]. Bark conversion by Eq. 7.9.
- Offset interpolated between NMT 6 dB and TMN 29 dB "for all partitions", compared with `minval`, and the larger value used.
- Pre-echo control and threshold in quiet (wording below).
- "The energy in each scale-factor band, `E_sf(sb)`, and the threshold in each scale-factor band, `T(sb)`, are calculated as described in [31]."

The equations:
- (7.10) `MSR(sb) = 10 log10(T(sb) / E_sf(sb))`.
- (7.11) `T'_n(sb) = H(Σ_{i=1, i≠n}^{N} s_i)`, where `H` is "all the mathematical transformations of the MPEG psychoacoustic model". This is the cross-adaptive step: the threshold for track `n` comes from the sum of the accompanying tracks, based on the cross-adaptive architecture of Reiss 2011 and Zölzer 2011 [2, 51]. For an ordered pair A → B, `T'_B = H(s_A)`.
- (7.12) `MSR_n(sb) = 10 log10(T'_sb / E_sf,n(sb))`. The numerator is typeset `T'_sb`; from Eq. 7.11 and the text it is `T'_n(sb)`. This is the margin the existing kernel computes.
- (7.13) `M_n = Σ_{sb : E_sf,n(sb) < T'_n(sb)} MSR_n(sb) / T_max`, with `T_max = 20 dB` (p. 109). Aggregation is out of scope here.

Experimental context [T]:
- The tracks are loudness-normalised (ITU-R BS.1770-2) to −24 LUFS (lead vocal −18 LUFS) before optimisation (§7.2.3.2).
- A gating system [132] is named as a possible remedy for the raised noise floor, but it was not used.
- The output is a "Finished Mono Mixdown" (Fig. 7.1); the stimuli are 30-s mono mixes (§7.2.6.2).

What the thesis does not state: sample rate, FFT length, hop, block-switching treatment, channel handling inside the metric, frame aggregation over time, and any implementation or code.

### 1.2 Discrepancies and unresolved provenance

- **Threshold wording [U].** The thesis (p. 107) says the partitioned threshold of the current frame "is compared with that of the two previous frames and the threshold in quiet. The maximum of three values is chosen to be the actual threshold." dist10 computes `thr = max(qthr, min(nb, 2·nb₋₁, 16·nb₋₂))` (ll. 613–614; `rpelev = 2`, `rpelev2 = 16`): a minimum over the current and scaled previous values, then a maximum with threshold in quiet. The two readings differ. A plain maximum over frames would hold a decaying masker's threshold for two frames. The dist10 rule caps a rising threshold, which is pre-echo control. This draft follows dist10 [P]. The thesis prose may be a loose description of the standard, or Ronan's code may differ. Without the code this cannot be decided. The burst onset (§3) shows where the readings diverge: there, dist10's threshold equals threshold in quiet.
- **Band-table provenance [U].** The thesis cites the AAC paper [31] for `E_sf`, `T` and `B(dz)`, and MPEG-1 [155] for the model. The stated 29/6 dB values are the MPEG-1 Layer III ones (LAME's `psymodel.c` labels 18/6 dB as AAC values). The thesis names no band table, number of bands or sample rate. This draft uses dist10's 21 long Layer III scale-factor bands with their `bu`/`bo`/`w1`/`w2` partition weights (`psy_data`) [P]. Whether Ronan used these bands, AAC bands or another table is unresolved.
- **Backend [P].** dist10 is our chosen backend because it is a citable, pinned, runnable implementation of the model the thesis names. It is not established as Ronan's implementation. Agreement with dist10 shows reproduction of dist10 only.

## 2. Draft numerical contract: masker thresholds and target energies on one grid

Output for an ordered pair A → B over one clip: two float64 arrays shaped `(valid frames, 21)`. `T[k, sb]` is `thm[sb]` of call `k` of model run R_A on the masker clip. `E[k, sb]` is `en[sb]` of call `k` of run R_B on the target clip. Both use the same energy unit, frame index and band index, so the margin kernel consumes them directly (Eq. 7.12).

### 2.1 Reference profile v1 [P]

| Item | Profile v1 | Deferred |
| --- | --- | --- |
| Sample rate | 44,100 Hz exactly; anything else is rejected, with no resampling. The `psy_data` row is selected by exact equality `sfreq == freq_tp` (l. 777). | 48 kHz (tables exist; needs its own fixture); 16–32 kHz |
| Channels | Mono only: exactly one channel. dist10 couples stereo channels through the shared age pointers, which flip only when `chn == 0` (ll. 447–459). | Stereo, and any fold-down rule |
| Input samples | int16 integer samples, used as the reference's `short` values with no rescaling (l. 474). Energies are unnormalised FFT energy of int16 counts. | Float-WAV conversion (scale, rounding, clipping) |
| Masker | Exactly one masker clip per pair, the same length as the target clip | Sums of several accompanying tracks (Eq. 7.11 with N > 2) |

### 2.2 Contract table

| Item | Contract | Label / source |
| --- | --- | --- |
| Cross-adaptive calculation | `T` from the model run on the masker, `E` from the run on the target; `MSR = 10 log10(T / E)` per band and frame. | [T] Eqs. 7.11–7.12 |
| Model | dist10 `L3psycho_anal`, long-block quantities `en[sb]`, `thm[sb]`, `SBMAX_l = 21`. | [S] ll. 652–665; [P] backend (§1.2) |
| Window and FFT | Hann `w[i] = 0.5·(1 − cos(2π(i − 0.5)/1024))`, i = 0…1023 (l. 183; the half-sample offset is in the source). 1024-point FFT, energy `|X|²` with no 1/N. In the last butterfly stage, `energy[i] ≤ 0.0005` is replaced by 0.0005 for the even index of each pair only (subs.c ll. 129–133). `FLOAT` is `float` under `-DUNIX`, so windowed samples, energy and phase are single precision; `eb`, `thr`, `en` and `thm` are double. | [S] |
| Hop and frame alignment | One call per 576-sample granule. Call `k` (first call `k = 0`, save buffer initially zero) analyses stream samples **[576k − 768, 576k + 256)**, with zeros before sample 0. The three short FFTs read `savebuf[256..767]` (l. 508), inside the same window. So call `k` depends only on samples `< 576k + 256` plus state from earlier calls. | [S] ll. 466–512; [R] impulse pair 1983/1984; [R] final-frame case |
| Current vs returned values | The function returns `ratio` before analysing the new granule (ll. 441–445), so the returned ratio of call `k` belongs to call `k − 1`. The contract uses the current call's `en`/`thm`. | [S]; [R] |
| Partitions, spreading, tonality | As dist10: `eb[b] = Σ energy[j]` over `partition_l`. Spreading `s3_l` (ll. 821–838). Unpredictability from long-FFT lines 0–5 and short-FFT lines 6–205; 0.4 above. `tb = clamp(−0.299 − 0.43·ln(max(0.01, ctb/ecb)), 0, 1)`; `SNR = max(minval, 29·tb + 6·(1 − tb))`. | [S] ll. 483–604; [T] prose agrees |
| Threshold | `nb = ecb·norm·10^(−SNR/10)`; `thr = max(qthr_l, min(nb, 2·nb₋₁, 16·nb₋₂))`. `qthr_l` per partition from `psy_data`. | [S] ll. 608–616; [U] thesis wording (§1.2) |
| Scale-factor bands | `en[sb] = w1·eb[bu] + w2·eb[bo] + Σ_{bu<b<bo} eb[b]`; `thm[sb]` the same with `thr`. | [S] ll. 652–660; [U] band-table provenance |
| Temporal state | `r`, `phi_sav`, `nb_1` and `nb_2` start at zero. For calls 0 and 1, `min(nb, 0, 0) = 0`, so `thr = qthr`. | [S] ll. 75, 613–614; [R] |
| **Final-frame rule** | For a clip of `N` samples, the front end runs `ceil(N / 576)` calls and fills a final partial granule with zeros, as the dist10 encoder does with a short final frame (`encode.c` ll. 160–162). Frame `k` is **valid iff `k ≥ 2` and `576k + 256 ≤ N`**: the long window lies inside the clip and the start-up state is past. Valid frames are therefore `k = 2 … ⌊(N − 256)/576⌋`, which is `⌊(N − 256)/576⌋ − 1` frames for `N ≥ 1408`. A clip with `N < 1408` has no valid frame, and its result is unavailable rather than computed from padding. The fill value cannot affect a valid frame, because its window ends inside the clip. Calls past the last valid frame are computed only to keep the state sequence identical; their values are discarded. Masker and target are cut to the same `N`, so their valid frames coincide. | [S] window extent; [R] final-frame case; [P] rule |
| Block switching | If `pe ≥ 1800` the encoder takes the short-block path, which overwrites `eb` and `thr` with short-block values and forms no long `en`/`thm` (ll. 628–690). The state `nb_1`/`nb_2` is updated before the branch in both paths (l. 616). Options: (a) mark such frames unavailable for both directions; (b) apply the long mapping to the long `eb`/`thr` before the branch (probe B, §3), recording `pe` and the encoder branch as evidence. Probe B equals the encoder bit for bit on all 69 long-path calls. On short-path calls it gives finite values, which at a masker onset are capped by pre-echo control (§3). Decision for review; revision 1 recommended (b). | [S]; [R] probe B; [P] open |
| Zero energy and target activity | The FFT floor makes `en[sb] > 0` for every input. For exact digital silence, `en` is the constant floor vector (`en[0] = 0.02553` … `en[20] = 0.05679` at 44.1 kHz), so the kernel's zero-energy mask never triggers. Exact target silence is still decidable from the input: a frame is exactly silent iff its window `[576k − 768, 576k + 256)` holds no nonzero int16 sample. In the fixture this coincides exactly with `en` equal to the floor vector, for all 92 probe B records. The front end must report this silent-window flag with `E`, rather than infer silence from `en`. How activity is gated, and any action threshold, is a separate decision. | [S] subs.c l. 130; [R] silent-masker control; [U] gate |
| Threshold in quiet | `T` is never below `thm_quiet[sb]` (the band mapping of `qthr_l`). For a silent masker `T = thm_quiet` exactly. The front end should report `thm_quiet` and the share of `thm` from partitions where threshold in quiet decided `thr`, so a margin can be traced to the masker or to absolute threshold. | [S] l. 614; [R] |
| Units and level | `T` and `E` are in unnormalised-FFT energy of int16 counts. The ratio is unit-free, but thresholds are not invariant to a common gain, because `qthr` and the int16 grid are absolute. Measure at the actual exported levels; declare any reproduction normalisation (Ronan: −24/−18 LUFS) separately. | [S]; [T] §7.2.3.2; [R] level case |

## 3. Independent reference evidence

Two observation-only patches of the same pinned `l3psy.c`, each built into its own harness with the reference makefile flags (`-g -DUNIX`) in `gcc@sha256:d47e63a2…cd525e` (GCC 13.5.0, x86_64) without network access. Full provenance is in `tests/fixtures/mpeg_psy_model2_dist10/provenance.json`.

- **Probe A, encoder-path capture** (`capture/l3psy_probe.diff`, outputs `expected/`). Unchanged from revision 1: it copies `en`/`thm` only where the encoder computes them; short-path calls are `null`. It runs only on clips that are whole 1152-sample frames.
- **Probe B, long-band mapping probe** (`capture/l3psy_mapping_probe.diff`, `capture/harness_mapping.c`, outputs `expected_long_mapping/`). It is a project extension, not encoder behaviour. Probe B records `temp_1 = min(nb, 2·nb₋₁, 16·nb₋₂)` per partition. Then, on every call, after `pe` and before the block-switching branch, it applies the encoder's long-band expressions in the encoder's summation order to:
  - `eb` and `thr`, giving `en` and `thm`;
  - `qthr_l`, giving `thm_quiet`;
  - `temp_1`, giving `thm_signal`;
  - `thr` restricted to partitions where `qthr_l ≥ temp_1`, giving `thm_from_quiet_partitions`.

  The harness also records the window bounds, whether the window ends inside the clip, and the count of nonzero int16 samples in the window. It accepts any clip length and zero-fills the final partial granule.

Reproducibility:
- Recapture from a fresh extraction of the pinned archive gave probe A outputs and a harness A binary byte-identical to the revision 1 capture.
- Two runs of both probes were byte-identical, in outputs and binaries.
- `capture/verify_fixture.py` regenerates the inputs, checks every hash, and checks the relations below exactly.
- `capture/summarize_controls.py` prints the numbers quoted here.

| Case | Inputs (44.1 kHz, mono int16) | What it exposes |
| --- | --- | --- |
| Normalisation and level | 1 kHz masker, amplitude 8192, and 2 kHz target, 1024 (12 granules); the same pair 40 dB lower (81.92 and 10.24 before rounding) | Absolute unit: loud masker call 6, `en[6] = 6.1268e12`; a [−1, 1]-scaled input would be 2³⁰ (90.3 dB) lower. Margins are not level-invariant (call 6, band 6: 95.0 dB loud vs 69.2 dB quiet). At −40 dB, threshold in quiet decides more of the masker threshold: at call 6, the share of `thm` from quiet-limited partitions in bands 0, 1 and 20 is 0.67, 0.58 and 0.76, against 0.18, 0.00 and 0.80 at the loud level. |
| Frame alignment | Single impulse 32767 at sample 1983 or 1984 (6 granules) | Call 3 covers [960, 1984): the impulse at 1983 raises call 3's `en[0]` from 0.0255 to 25.07, the one at 1984 does not. |
| Final frame | The 2 kHz target cut to `N = 6536 = 11·576 + 200` samples, against the full 6912-sample target | Calls 0–10, whose windows end at or before 6016, are bit-identical to the full clip in `pe`, `en`, `thm`, `thm_quiet`, `thm_signal` and nonzero count. Call 11's window ends at 6592 > N, includes 56 fill zeros and differs. Valid frames under §2.2: k = 2…10. |
| Onset and block switching | Silence, then a 1 kHz burst at amplitude 8192 from sample 2000 (8 granules) | The encoder takes the short path at calls 4–5 (`pe` 9219, 2432), so probe A has `null`. Probe B gives finite values there. At call 4 (559 nonzero samples in the window), band 6 has `en = 3.066e12` but `thm = 0.04799 = thm_quiet`: pre-echo control caps `thr` at `2·nb₋₁` and `16·nb₋₂` from the silent calls, so threshold in quiet decides it. Under option (b), a masker onset frame therefore reports no masking from the masker. Call 6 resumes on the long path with `thm[6] = 1.556e12`. Probe B equals probe A on all 69 long-path calls of the eight whole-frame inputs (80 calls). The short-path calls are 11: calls 0–1 for the loud masker, the loud target and the quiet masker; call 1 for the quiet target; calls 4–5 for the burst; call 4 for each impulse. |
| Silent-masker control | Digital silence (12 granules) as masker against the active 2 kHz targets; silence also as target | Silent masker: `thm == thm_quiet == thm_from_quiet_partitions` on every call. The signal-derived part `thm_signal` is at most 7.6·10⁻⁴ of `thm_quiet` (≥ 31 dB below). So the result is threshold in quiet alone. Against the active targets, calls 2–11, no cell has `T > E` (0/210). Margins run from −120.6 to −5.9 dB for the target at 1024, and −80.6 to −7.2 dB for the target at 10.24; band 20 comes closest. Silent **target**: the floor energy is not zero, so every cell counts as masked. Loud masker → silence: 210/210 cells, 28.7 to 147.7 dB. Silence → silence: 210/210 cells, 12.6 to 40.7 dB, because threshold in quiet lies above the FFT floor. The window's nonzero-sample count separates these frames exactly; `en` alone gives only an indirect sign of silence. |

Limits: one reference build, one rate, mono, synthetic tones and impulses (unit-test signals, not evaluation data). It verifies reproduction of dist10, not the validity of the model or agreement with Ronan's implementation. Float32 FFT arithmetic means a float64 reimplementation can match only within a tolerance derived from data; that tolerance is not set yet [U]. Probe B's short-path values are a project extension, with no encoder counterpart to compare them against.

## 4. Remaining implementation decisions and open questions

1. **Block-switching option** (a) or (b) (§2.2). Under (b), decide whether onset frames whose threshold is capped by pre-echo control need their own label in the evidence.
2. **Threshold wording**: confirm with the owner that dist10's `max(qthr, min(…))` is acceptable as the backend reading of the thesis's "maximum of three values". Otherwise specify an alternative and its source.
3. **Band table**: Layer III long bands (this draft) versus AAC bands. This needs the author code or a statement from the authors.
4. **Target-activity gate and action thresholds**: separate decision. The front end supplies the exact silent-window flag, `thm_quiet` and the quiet share; it does not gate.
5. **Reimplementation tolerance** against the float32 reference, set from fixture residuals in the production increment.
6. **Deferred profile items**: float-WAV conversion, stereo, 48 kHz (needs a fixture), multi-masker sums (sum domain and clipping).
7. **Time aggregation** of per-frame margins: the thesis states none; Eq. 7.13 is per band. Out of scope here.
8. Retry the SoundSoftware author repository; if it is reachable, compare its threshold and band code with dist10.
