# MPEG psychoacoustic front end for directional masking: source trace and draft numerical contract

Status: draft for Codex review (Task 2 of the directional-masking-margin work), revision 2 (2026-11-11). Source tracing and reference evidence only: no production front end, analyzer integration or report change. It follows the pairwise-masking reproduction spec (Codex project folder, `2026-11-11-pairwise-masking-reproduction.md`, section "MPEG front end"). The existing kernel `adapters.dsp_analysis.masking.directional_masking_margin_db` (commit `4d620a0`) consumes the two arrays this front end must produce.

Revision 2 supersedes these statements of revision 1: the thesis is now read (§1), so the equation numbering is the thesis's and is no longer unresolved. The first profile is pinned to 44.1 kHz, mono, int16 (§2); revision 1 proposed 44.1 and 48 kHz and a float-input rule. The end-of-stream rule is replaced by the final-frame rule (§2.2). The short-block path is now instrumented by a separate probe (§3).

Revision 3 (2026-11-12, Task 3B) adds the long-window FFT reference capture (§3.1). It corrects revision 2's statement that the floor applies to "the even index of each pair only". That index is the FFT's pre-reorder position. In natural bin order the floor can only hit bins 0–511; it never hits Nyquist (512) or bins 513–1023. The capture confirms this.

Revision 4 (2026-11-12, Task 3D) adds the short-window FFT and unpredictability reference capture (§3.2). It traces the three short-block offsets, the short-bin mapping of lines 6–205, the two-slot long-predictor history and the constant lines, and records that the zero-denominator branch is unreachable.

Revision 5 (2026-11-12, Task 3G) adds the partition-input capture (§3.3). Bins 465–512 are not covered by the 44.1 kHz long table and accumulate into partition 0, and the short table overwrites `numlines[0..38]`.

Revision 6 (2026-11-12, Task 3I) adds the spreading and tonality-input capture (§3.4). It covers the matrix orientation (row = output), the float32 `ecb` store, the 0.01 lower clamp, and the zero-energy guard, which is unreachable.

Revision 7 (2026-11-16, Task 3K) adds the partition-threshold capture (§3.5): the `minval`, `qthr` and `norm` tables, the tonality factor and its clamp, the SNR, the float32 `nb`, the two-call pre-echo history, and `thr` before band mapping. It makes the §2.2 "Threshold" and "Temporal state" rows precise: the history stores the unbounded float32 `nb`, not `thr`, and is updated on short-path calls too.

Revision 8 (2026-11-16, Task 3M) adds the long scale-factor-band mapping evidence (§3.6): the `psy_data` band rows, the endpoint weights, the interior range and summation order, a capture of the mapping inputs on every call (probe H), and the directional margins of the package chain against probe B. It makes the §2.2 "Scale-factor bands" row precise. The band-table question of §4 item 3 (Layer III against AAC bands) is unchanged.

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
| Window and FFT | Hann `w[i] = 0.5·(1 − cos(2π(i − 0.5)/1024))`, i = 0…1023 (l. 183; the half-sample offset is in the source). 1024-point FFT, energy `|X|²` with no 1/N. In the last butterfly stage, `energy[i] ≤ 0.0005` is replaced by `0.0005` for the even pre-reorder position `i` of each pair (subs.c ll. 129–133). After the bit-reversal reorder these are the natural bins 0–511 (§3.1; revision 2 wording corrected). `FLOAT` is `float` under `-DUNIX`, so windowed samples, energy and phase are single precision; `eb`, `thr`, `en` and `thm` are double. Stage-by-stage conventions are in §3.1. | [S]; [R] §3.1 |
| Hop and frame alignment | One call per 576-sample granule. Call `k` (first call `k = 0`, save buffer initially zero) analyses stream samples **[576k − 768, 576k + 256)**, with zeros before sample 0. The three short FFTs read `savebuf[256..767]` (l. 508), inside the same window. So call `k` depends only on samples `< 576k + 256` plus state from earlier calls. | [S] ll. 466–512; [R] impulse pair 1983/1984; [R] final-frame case |
| Current vs returned values | The function returns `ratio` before analysing the new granule (ll. 441–445), so the returned ratio of call `k` belongs to call `k − 1`. The contract uses the current call's `en`/`thm`. | [S]; [R] |
| Partitions, spreading, tonality | As dist10: `eb[b] = Σ energy[j]` over `partition_l`. Spreading `s3_l` (ll. 821–838). Unpredictability from long-FFT lines 0–5 and short-FFT lines 6–205; 0.4 above. `tb = clamp(−0.299 − 0.43·ln(max(0.01, ctb/ecb)), 0, 1)`; `SNR = max(minval, 29·tb + 6·(1 − tb))`. | [S] ll. 483–604; [T] prose agrees |
| Threshold | `nb = ecb·norm·10^(−SNR/10)`; `thr = max(qthr_l, min(nb, 2·nb₋₁, 16·nb₋₂))`. `qthr_l` per partition from `psy_data`. | [S] ll. 608–616; [U] thesis wording (§1.2) |
| Scale-factor bands | `en[sb] = w1·eb[bu] + w2·eb[bo] + Σ_{bu<b<bo} eb[b]`; `thm[sb]` the same with `thr`. The endpoint terms are summed first, then the interior partitions in ascending order (double). `thr` is the final partition threshold, after the pre-echo limit and the quiet floor. Tables and evidence are in §3.6. | [S] ll. 652–660, 938–968; [R] §3.6; [U] band-table provenance |
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

### 3.1 Long-window FFT stage (probe C, Task 3B)

**Probe C** is a third observation-only patch (`capture/l3psy_subs_fft_probe.diff`, generated by `capture/make_probe_fft.py`, built with `capture/harness_fft.c` by `capture/run_fft_in_container.sh`; outputs `expected_long_fft/`). It is built on probe B's `l3psy.c`, so the same binary also prints probe B's long-band fields for every call. What it records:
- In `L3psycho_anal`, around the Layer III long `fft()` call (original l. 483):
  - before the call: `window[0..1023]` and `wsamp_r[0..1023]`, the windowed samples (the FFT overwrites this array in place);
  - after the call: `energy[0..1023]` and `phi[0..1023]` as returned.
- Inside `fft()` (`subs.c`), only while armed by that call: each `energy[i]`/`energy[ip]` right after it is computed and before the floor test. The same bit-reversal swap is applied to this copy, so it ends in natural bin order.

Probe C writes only probe globals and block-local counters. The short FFTs and Layers I/II never arm it.

Selected calls, chosen to expose each failure mode:

| Input | Calls | Purpose |
| --- | --- | --- |
| `silence_12granules` | 2 | floor on an all-zero window |
| `impulse_32767_at1983` | 3, 4 | window edge and frame offset |
| `impulse_32767_at1984` | 3, 4 | window edge and frame offset |
| `masker_1k_a8192`, `masker_1k_a81.92`, `target_2k_a1024`, `target_2k_a10.24` | 6 | tones at two levels: scaling, floor inside an active window |

Each selected call records `windowed_samples`, `energy_prefloor`, `energy` and `phi`, 1024 values each, printed with `%.9g` (an exact float32 round trip). The window coefficients are in `expected_long_fft/window_long.json`. All nine runs wrote byte-identical copies; one is kept.

**Conventions, from the source and checked against the capture.** All checks are exact unless a tolerance is given.

| Stage | Source behaviour | Precision | Capture evidence |
| --- | --- | --- | --- |
| Window | `window[i] = 0.5·(1 − cos(2·PI·(i − 0.5)/1024))` with `PI = 3.14159265358979` (common.h l. 195), computed in double, stored as `FLOAT`. The window is symmetric about i = 0.5 and i = 512.5. It is neither the periodic Hann window (w[0] = 0) nor the symmetric one (w[1023] = 0). | float32 | Equal to float32 of the formula. The truncated `PI` changes no coefficient against full-precision π. w[0] = w[1] = 2.3530952e−6, w[512] = w[513] = 0.9999977, w[1023] = 2.1177724e−5. |
| Windowed samples | `wsamp_r[j] = window[j] * savebuf[j]` for j = 0…1023: a float32 product of `FLOAT` and an int16 value. `savebuf[j]` is stream sample 576k − 768 + j (zeros outside the clip), so position j of call k is that stream sample. `wsamp_i = 0`. | float32 | Equal to float32(window · frame samples), with the frame rebuilt independently from the raw inputs. Position j matches `framing.windows[k, j]`. |
| FFT | Radix-2 decimation in frequency on natural-order input. Twiddles come from a double recurrence seeded with cos/sin of `PI/le1`. Stage values are stored as float32 between stages; products with twiddles are formed in double and then stored. Forward transform `X[k] = Σ x[n] e^{−2πikn/1024}`, unnormalised (no 1/N). A bit-reversal reorder at the end gives natural bin order k = 0…1023 (k = 0 is DC, k = 512 is Nyquist, 513–1023 mirror 511–1). | float32 storage, double twiddles | Against a float64 FFT of the captured windowed samples: \|pre − \|X\|²\| ≤ 9.7e−8 of the call's peak energy; Parseval ratio within 3.7e−8 of 1; phase within 2.4e−5 rad on bins above 1e−6 of the peak. For an impulse of windowed value v, every bin has energy v² within 2.5e−7 relative, and phase −2πkp/1024 within 1.7e−7 rad. Example: 1983 → call 3, p = 1023, v² = 0.4815. Per-bin relative agreement in low-energy bins is not meaningful, because float32 noise scales with the peak. |
| Energy | `energy[i] = x_real²+x_imag²` in float arithmetic (all operands `FLOAT`). | float32 | `energy_prefloor` captured at that point. |
| Floor | Final stage, even pre-reorder position i only: `if (energy[i] <= 0.0005) {phi[i] = 0; energy[i] = 0.0005;}`. The test compares float32 energy promoted to double against double 0.0005, and stores float32(0.0005) = 5.000000237e−4. For the odd position ip: no floor; `phi = 0` if `energy == 0`, else `atan2`. A 10-bit bit reversal maps even positions exactly to natural bins k < 512. | float32 | Energy changes exactly on bins k < 512 with float64(pre) ≤ 0.0005, to float32(0.0005) with phi = 0. Silence: bins 0–511 floored; 512–1023 remain 0 with phi = 0. The quiet 2 kHz target (call 6): bins 169 and 170 (pre 3.7e−4 and 5.2e−5) are floored inside an active window; their mirror bins 854–855 are not. |
| Phase | `phi = atan2(x_imag, x_real)` in double, stored as `FLOAT`. Floored bins and zero-energy odd bins have phase 0. | float32 | Nyquist phase is π for the 1983 impulse (X[512] = −v) and 0 for 1984. |
| Consumed bins | `L3psycho_anal` uses `energy[j]` for j = 0…512 (`HBLKSIZE = 513`: partitions, `eb`, `cb`). It uses `phi[j]` and `sqrt(energy[j])` only for j = 0…5 (long-FFT unpredictability). Bins 513–1023 are never read. | — | [S] ll. 486–565 |

**Downstream unchanged.** Every probe C line carries probe B's fields equal to `expected_long_mapping`, for all calls of the seven stored inputs. For the two inputs without selected calls (burst, truncated target), the whole probe C output was byte-identical to probe B's.

**Which comparison exposes which error.** I injected each error into a temporary copy of the evidence; the listed checks fail:

| Error | Exposing comparison |
| --- | --- |
| Window offset or other Hann variant (periodic, symmetric) | Window coefficients against the formula; windowed samples against window · frame; the 1983 impulse energy at call 3 (0.4815 = (w[1023]·32767)²; periodic Hann gives 0.0951, symmetric Hann 0) |
| Frame offset by one sample | Windowed samples against the rebuilt frame. The impulse position checks: 1983 at p = 1023 of call 3 and p = 447 of call 4; 1984 absent from call 3 and at p = 448 of call 4. The phase slope against −2πkp/1024 |
| FFT scaling (1/N, 1/√N or a missing factor) | Pre-floor energy against \|X\|²; Parseval; impulse energy v² in every bin; the floor set, which a scaled spectrum shifts (for example, the tones gain floored bins) |
| Floor on the wrong bins (even natural bins, all bins, Nyquist) | Exact floor set: k < 512 with energy ≤ 0.0005. Silence leaves 512–1023 at 0. The quiet target floors exactly bins 169–170 and not their mirrors |
| Phase sign convention | Phase against angle(X) of the forward transform |

The floor value itself cannot be told apart at the capture's precision. A float64 pipeline that stores 0.0005 differs from float32(0.0005) by 2.4e−11, which vanishes when the result is rounded to float32. The contract specifies float32(0.0005) because that is what dist10 stores.

Limits: nine calls of synthetic signals at one rate. The tolerances above describe the float32 reference against a float64 FFT; they are not yet the tolerance for a reimplementation (§4). The short FFTs, tonality, spreading and thresholds are not covered by probe C.

### 3.2 Short-window FFTs and unpredictability (probe D, Task 3D, 2026-11-12)

**Probe D** is a fourth observation-only patch, built on probe C (`capture/l3psy_subs_short_probe.diff` from `capture/make_probe_short.py`, `capture/harness_short.c`, `capture/run_short_in_container.sh`; outputs `expected_short/`). One binary prints probe B's fields and probe C's arrays exactly as before, and appends for the selected calls:
- `short_windowed_samples`: the three short-FFT inputs, 3 × 256;
- `short_energy_prefloor`, `short_energy` and `short_phi`: 3 × 129, natural short bins 0–128;
- `long_history`: the ages `new`/`old`/`oldest`; `r` and `phi_sav` for bins 0–5, `old` and `oldest` before the long predictor loop, `new` after it;
- `cw[0..512]`: before partitioning.

The short Hann coefficients are in `expected_short/window_short.json`. Probe D writes only its own variables. The short-FFT pre-floor copy is armed only by the three short-block calls.

Selected calls are consecutive, so the history can be checked call to call:

| Input | Calls | Purpose |
| --- | --- | --- |
| `silence_12granules` | 0–2 | zero, one-call and established history |
| `impulse_32767_at1983` | 2–5 | short-block offsets |
| `burst_1k_a8192_onset2000` | 3–5 | onset; calls 4–5 take the short-block path |
| `masker_1k_a8192` | 0–4 | sustained tone: start-up and established predictor |
| `target_2k_a10.24` | 6 | partially floored active short block (block 2, bins 42 and 86), added in the Task 3D extension |

**Traced source behaviour, checked against the capture:**

| Item | Source behaviour (l3psy.c, original lines) | Precision | Capture evidence |
| --- | --- | --- | --- |
| Short window | `window_s[j] = 0.5·(1 − cos(2·PI·(j − 0.5)/256))`, j = 0…255 (l. 184), the same half-sample offset as the long window | float32 | Equal to float32 of the formula |
| Short blocks | Block s = 0, 1, 2: `wsamp_r[j] = window_s[j] * savebuf[128·(2 + s) + j]` (ll. 506–512). In stream samples, call k's blocks are **[576k − 512 + 128s, +256)**: [576k−512, 576k−256), [576k−384, 576k−128), [576k−256, 576k). All three lie inside the long window [576k − 768, 576k + 256); block 1 has the same centre (576k − 256) | float32 product | Exact for every block. The 1983 impulse appears at positions 191 and 63 of call 4's blocks 0 and 1, not in block 2 (which starts at 2048), and in no block of call 3, although call 3's long window holds it |
| Short FFT | The same `fft()` as the long FFT with N = 256: unnormalised forward transform, natural bins after an 8-bit bit reversal. Floor `energy ≤ 0.0005` → float32(0.0005), phase 0, on even pre-reorder positions = **natural short bins 0–127**; bin 128 (Nyquist) and 129–255 (mirrors) are never floored. Odd positions with zero energy get phase 0 | float32 storage | Pre-floor energy within 1.7e−7 of each block's peak against a float64 FFT; phase on strong unfloored bins within 1e−4 rad; floor set exact: silent blocks floor 0–127 and leave 128 at 0 |
| Long predictor, bins 0–5 | `r[chn][age][j]` and `phi_sav[chn][age][j]` are float32 with **two** age slots. Each call (chn 0) first flips `new`/`old`/`oldest`: from the initial 0/1/0, call k has new = oldest = 1 − k mod 2 and old = k mod 2 (ll. 447–459). `oldest` aliases the `new` slot, read before it is overwritten, so it holds the value from two calls back. `r' = 2·r_old − r_oldest`, `φ' = 2·φ_old − φ_oldest`; `r_new = sqrt(energy[j])` of the floored long energy, `φ_new = phi[j]` (ll. 484–501). History starts at zero (ll. 186–189) | r, φ float32; r', φ', cw double | Ages alternate as stated. Each call's `old`/`oldest` equal the values stored by calls k − 1 / k − 2, and zero before call 0. `r_new` = float32(√energy) and `φ_new` = long phase where probe C captured the call. Silence gives cw[0..5] = 1, 1/3 and 0 at calls 0, 1 and 2 (r' = 0, then 2r, then r); the tone gives cw[0..5] = 1 at call 0 |
| Short predictor, lines 6–205 | For j = 6, 10, …, 202: short bin `k = (j + 2)/4` (integer division; k = 2…51). `r' = 2·sqrt(e_s[0][k]) − sqrt(e_s[2][k])`, `φ' = 2·φ_s[0][k] − φ_s[2][k]`, compared with block 1: `r2 = sqrt(e_s[1][k])`, `φ2 = φ_s[1][k]`. `cw[j] = cw[j+1] = cw[j+2] = cw[j+3]` (ll. 521–539). This is an **interpolation within the current call** (blocks 0 and 2 around block 1), using no history from earlier calls | double from float32 inputs | Constant groups of four; reconstruction within 1e−12 (at most 2.2e−16 observed); silent short blocks give cw = 0 |
| Constant lines | `cw[j] = 0.4` for j = 206…512 (ll. 545–546) | double | Exact on every call |
| Unpredictability formula | `cw = sqrt((r_new cos φ_new − r' cos φ')² + (r_new sin φ_new − r' sin φ')²) / (r_new + |r'|)`. **Zero-denominator branch:** `if (temp3 != 0) … else cw = 0` in both predictors | double | Never taken. The denominator is at least `r_new` (or `r2`), which is at least √float32(0.0005) ≈ 0.02236, because bins 0–5 and short bins 2–51 are always floored when weaker. The branch is unreachable for any input; the capture's smallest denominator is positive |
| Use | `cw` feeds `cb[tp] += cw[j]·energy[j]` in the partition step (ll. 553–565), out of scope here. `cw` is computed on every call, before the block-switching decision, so short-path calls (burst 4–5, tone 0–1) have it too | — | Captured on those calls |

**Downstream unchanged.** With probe D fields removed, every line of the four stored inputs equals the stored probe C or probe B line. For the five inputs without probe D calls, the whole output was byte-identical to the stored probe C or B file.

**Which comparison detects which error.** I swapped each traced rule in the verifier for a wrong variant; the listed checks fail:

| Wrong rule | Detected by |
| --- | --- |
| Short-block offset (+1 sample, blocks from the window start `128s`, blocks on the new granule `128(2+s) + 256`) | Exact windowed-sample comparison against the raw input (20–23 failures); the impulse positions 191/63 |
| Short bin mapping (`j/4`, `(j+2)/4 + 1`) | `cw` reconstruction on the 7 calls with active short blocks |
| Short predictor block order (2·b2 − b0 for b1; extrapolating b2 from b0, b1) | `cw` reconstruction (7 calls) |
| History order (old/oldest swapped, both from the previous call, one call late) | Stored-history identities (`old` = call k − 1, `oldest` = call k − 2, zero before call 0) and `cw` (8–10 calls); the silence sequence 1, 1/3, 0 |

**Left uncovered:**
- The zero-denominator branch, which is unreachable, as argued above.
- ~~A partially floored active short block~~: covered since the Task 3D extension (2026-11-12). The quiet 2 kHz target (`target_2k_a10.24`) call 6, block 2 floors exactly short bins 42 and 86, at pre-floor energy ≤ 1.27e−4, while all other bins 0–127 have ≥ 2.06e−2. Floored bins take float32(0.0005) with phase 0, and Nyquist is untouched.
- Stereo, where the ages flip only on chn 0. Outside profile v1.
- History through more than one short-path call in a row, after an established tone; the burst covers onset only.

Limits: 15 calls, one rate, synthetic signals. The tolerances describe the float32 reference, not a reimplementation tolerance.

**Implementation (Task 3F, 2026-11-12).** `adapters.dsp_analysis.unpredictability.dist10_unpredictability` computes `cw` `(calls, 513)` in float64 from the post-floor long and short spectra. It uses the two float32 history slots, read before overwrite, with zero history at the start of each clip. Lines 6–205 come from short bin `(j + 2) // 4`, predicting block 1 from blocks 0 and 2. Lines 206–512 are 0.4. The zero-denominator branch returns 0. Two comparisons are kept apart:
- **Captured spectra (predictor alone):** the result is within 2.2e−16 of the captured `cw`. This holds for consecutive calls from call 0 (silence 0–2, tone 0–4) and for single calls from recorded history.
- **Python spectra (FFT precision included):** deviations grow as the bin's energy falls relative to its block or call peak.

  | Bins (weakest input relative to peak) | Short worst | Long worst |
  | --- | --- | --- |
  | Strong (≥ 1e−6) | 5.2e−6 | 6.9e−6 |
  | 1e−9 to 1e−6 | 2.1e−4 | 2.1e−4 (below 1e−6) |
  | Below 1e−9 | 6.9e−3 (burst call 5, line 170, short bin 43, energy 1.2e−11 of peak) | — |

  The long worst case is the masker, call 4, bin 5 (9.4e−10 of peak). In those bins, the reviewed spectrum agreement (energy within 1e−6 of the peak) allows relative energy differences of about 0.3–0.6% and phase differences of a few mrad.

No end-to-end `cw` tolerance is set. Whether weak-bin `cw` differences matter depends on the later partition step, which weights `cw` by energy (`cb += cw·energy`).

### 3.3 Partition inputs `eb` and `cb` (probe E, Task 3G, 2026-11-12)

**Probe E** is a fifth observation-only patch, built on probe D (`capture/l3psy_partition_probe.diff` from `capture/make_probe_partition.py`, `capture/harness_partition.c`, `capture/run_partition_in_container.sh`; outputs `expected_partition/`). After the long partition loop and before spreading, it copies `eb[0..62]`, `cb[0..62]`, the `energy[0..512]` and `cw[0..512]` the loop read, `partition_l[0..512]` and `numlines[0..62]`.

Selected calls are a subset of probe D's:

| Input | Calls | Purpose |
| --- | --- | --- |
| `silence_12granules` | 0–2 | silence and start-up |
| `impulse_32767_at1983` | 3–4 | impulse onset |
| `burst_1k_a8192_onset2000` | 4 | burst onset |
| `masker_1k_a8192` | 3 | established tone |
| `target_2k_a10.24` | 6 | quiet tone with the partially floored short block |

To keep the fixture small, only the probe E fields are stored (93 KB). With them removed, every input's probe E output was byte-identical to its stored probe D, C or B file; the verifier checks this through the recorded hashes.

**Traced source behaviour, checked against the capture:**

| Item | Source behaviour (l3psy.c, original lines) | Precision | Capture evidence |
| --- | --- | --- | --- |
| Table | `L3para_read` reads `tables/psy_data` (SHA-256 `9094946e…`), block `freq = 44100.0 partition = 62`: 63 long partitions `No=0..62` with `#lines` 1 (×15), 2 (×13), 3 (×7), 4 (×3), 5 (×3), 6, 6, 7, 7, 7, 9, 9, 9, 10, 10, 11, 14, 14, 15, 20, 20, 21, 27, 27, 36, 73, 18. **They sum to 465.** Then `partition_l[k2++] = i` for each line (ll. 781–790) | int | `partition_l` equals these counts in order |
| Unassigned bins | `partition_l` is a `static int` array, so entries 465–512 (≈ 20.03–22.05 kHz, **Nyquist included**) keep their zero initialisation. The loop's `if (tp >= 0)` never rejects a bin, so they are accumulated into **partition 0**, together with DC. Partition 0 holds 49 bins; no table entry is unused | — | `partition_l[465..512] = 0`. For the 1983 impulse at call 3 (flat spectrum), eb[0]/eb[1] = 49 |
| `numlines` | After the long table, `L3para_read` reads the short table (`freq = 44100.0 partition = 38`, 39 partitions, 109 lines) into the **same** `numlines` array (l. 869). So at partition time `numlines[0..38]` holds short-partition counts and `numlines[39..62]` long ones. `eb`/`cb` are unaffected, because `partition_l` was built earlier; the perceptual-entropy sum `pe -= numlines[b]·…` (l. 625) uses the overwritten values. That matters for the block-switching stage | int | Captured `numlines` = short table, then long table entries 39–62 |
| Accumulation | `eb[b] = cb[b] = 0` for b < 63, then for j = 0…512 in order: `eb[tp] += energy[j]; cb[tp] += cw[j] * energy[j]` (ll. 553–565) | `eb` is `static double`: double accumulation of the float32 energies. `cb` is a `FLOAT*` work array: a double product and sum, **stored as float32 after every addition** | Reconstruction in bin order with float32 stores is exact on all 8 calls. A double-only `cb`, or a mapping with all partition boundaries moved by one bin, fails to reproduce the capture |
| Inputs | `energy` is the long post-floor energy and `cw` the unpredictability (§3.1, §3.2) | float32; double | `partition_energy` = probe C energy where probe C captured the call, and float32(√) of bins 0–5 = probe D `r_new`; `partition_cw` = probe D `cw` |
| Silence | All bins below 512 are floored, Nyquist is 0 | — | eb[b] = #lines(b) × float32(0.0005); partition 0 gets 48 floored bins (DC, 465–511) |

**Comparisons** (`capture/compare_partition_inputs.py`; strata by eb / max(eb) of the call):

| Reconstruction | Quantity | Strong (≥ 1e−6) | Weak (1e−9–1e−6) | Very weak (< 1e−9) |
| --- | --- | --- | --- | --- |
| A: captured energy + captured `cw` | eb, cb | exact (8/8 calls) | exact | exact |
| B: Python spectra + Python `cw` | eb, relative | 1.8e−5 (quiet target, call 6, p16) | 2.6e−4 (masker, call 3, p27) | 4.9e−3 (masker, call 3, p43) |
| B | cb, relative | **2.5e−3** (burst, call 4, p50) | 2.6e−4 (masker, call 3, p27) | 4.6e−3 (masker, call 3, p49) |
| C: captured energy + Python `cw` | eb | exact | exact | exact |
| C | cb, relative | **2.5e−3** (burst, call 4, p50) | 1.7e−4 (quiet target, call 6, p1) | 4.7e−3 (masker, call 3, p49) |

Largest absolute errors: eb 2.6e5 (burst, call 4, p19) and cb 6.4e5 (burst, call 4, p50), both in strong partitions. Their relative size is given in the table.

**Finding for review:**
- In strong partitions, the `cb` error comes from `cw`, not from the long FFT: run C reproduces run B's strong-partition `cb` error with exact long energies.
- Mechanism: in burst call 4, partition 50 (long bins 159–168, 3.5e−5 of the call's peak) takes `cw` from short bins 40–42. There, short block 2 is 1e−11–1e−12 of its own peak, so the Python short spectrum's `cw` differs by up to 3.0e−3.
- Weighting by energy therefore does not damp `cw` differences when the long partition is strong but the short bins feeding its `cw` are weak.
- The end-to-end tolerance stays open until the effect on the threshold calculation is measured. In dist10, `cb` enters only through the tonality ratio `ctb/ecb` after spreading.

**Left uncovered:** stereo, and rates other than 44.1 kHz (other rates have their own `#lines` sums and unassigned ranges). Also not checked here: how the overwritten `numlines` affects `pe` and block switching.

**Implementation (Task 3H, 2026-11-12).** `adapters.dsp_analysis.partitions.dist10_partition_sums(energy, cw)` takes float32 `(calls, 513)` energies and float64 `cw` and returns float64 `eb` and float32 `cb`, each `(calls, 63)`.
- **Mapping:** the 44.1 kHz `#lines` are embedded with their `psy_data` provenance and checked against the Probe E mapping. Bins 465–512 go to partition 0.
- **Arithmetic:** ascending bin order; `eb` in float64; `cb` formed in float64 and rounded to float32 after every bin; calls processed in chunks.
- **Against Probe E:** exact on all 8 captured calls, from their captured inputs.
- **Python chain** (this package's spectra and `cw`), report only: strong-partition worst relative residuals are `eb` 1.8e−5 (quiet target, call 6, p16) and `cb` 2.5e−3 (burst, call 4, p50).
- **Retained case:** burst call 4, partition 50 is kept as the named case for the threshold comparison. The tolerance remains open.
- **Overflow:** finite inputs whose `cb` sum exceeds the float32 range are rejected with `ValueError`, naming the first call and partition, whatever the caller's NumPy error policy. The reference's double-to-float store is undefined behaviour in C for out-of-range values. `eb` cannot overflow, since 513 × float32 max is about 1.7e41. Profile v1 int16 inputs stay far below the float32 limit: the largest possible windowed-FFT energy is about 1.1e15.

### 3.4 Spreading and tonality inputs `ecb`, `ctb` and `ctb/ecb` (probe F, Task 3I, 2026-11-12)

**Probe F** is a sixth observation-only patch, built on probe E (`capture/l3psy_spreading_probe.diff` from `capture/make_probe_spreading.py`, `capture/harness_spreading.c`, `capture/run_spreading_in_container.sh`; outputs `expected_spreading/`). It records:
- after the long convolution: `ecb`, `ctb` and the matrix `s3_l` (stored once, in `s3_44100.json`, with the `psy_data` `bval` strings);
- in the tonality loop: whether the division ran, `ctb/ecb`, the value after the lower clamp, and the value passed to `tbb`.

Calls: all eight probe E calls, which include silence and start-up, the impulse and burst onsets (burst call 4), the established tone and the quiet tone. Only the F fields are stored (65 KB with the matrix). Every input's output, with the E and F fields removed, was byte-identical to its stored D, C or B file, and the E fields equalled `expected_partition`.

**Traced source behaviour, checked against the capture:**

| Item | Source behaviour (l3psy.c, original lines) | Precision | Capture evidence |
| --- | --- | --- | --- |
| Matrix | `L3para_read` (ll. 819–838), over `part_max = cbmax = 63` at 44.1 kHz:<br>`tempx = (bval[i] − bval[j]) × (3.0 if j ≥ i else 1.5)`;<br>`x = 8(t² − 2t)` with `t = tempx − 0.5` when 0.5 ≤ tempx ≤ 2.5, else 0;<br>`tempx += 0.474`;<br>`tempy = 15.811389 + 7.5·tempx − 17.5·√(1 + tempx²)`;<br>**`s3_l[i][j] = 0` if `tempy ≤ −60`** (the cutoff tests `tempy` alone, before `x` is added), else `exp((x + tempy)·LN_TO_LOG10)`, with the truncated `LN_TO_LOG10 = 0.2302585093` (common.h l. 199). `bval` comes from `psy_data`. **Row i is the output (centre) partition, column j the input (masker) partition.** Spreading toward lower output partitions (j > i) is twice as steep as toward higher ones | double | 3,114 of 3,969 entries are zero, with the same zero pattern as the formula on the `psy_data` `bval`; the values agree within 1.8e−16 relative (libm); the diagonal is about 1; the matrix is not symmetric |
| Convolution | `ecb[b] = ctb[b] = 0`; for b = 0…62, k = 0…62: `ecb[b] += s3_l[b][k]·eb[k]; ctb[b] += s3_l[b][k]·cb[k]` (ll. 573–586) | `ecb` is a `FLOAT*` work array: double product and sum, **stored as float32 after every term**. `ctb` is `static double` | Reconstruction from the probe E `eb`/`cb` is exact on 8 of 8 calls. A transposed matrix fails on every call, and a double-only `ecb` fails |
| Ratio | `if (ecb[b] != 0.0) { cbb = ctb[b]/ecb[b]; if (cbb < 0.01) cbb = 0.01; cbb = log(cbb); } else cbb = 0.0;` then `tbb = −0.299 − 0.43·cbb`, clamped to [0, 1] (ll. 591–602). There is a lower clamp only; no upper clamp | double | Ratio and clamp are exact. The tonality input is log of the clamped ratio within 5.6e−17. The clamp is active in silence and start-up (43–46 partitions per call, where `cw` = 0 makes `ctb` 0) and in impulse call 3 |
| Zero-energy guard | The division is skipped when `ecb[b] == 0` | — | Never taken: 63 of 63 divided in every call. Floored energies make every `eb > 0`, and `s3_l[b][b] ≈ 1`, so `ecb > 0`. Unreachable for profile v1 input |

**Comparisons** (`capture/compare_spreading_inputs.py`):

| Run | Quantity | Worst | Where (input, call, output partition) |
| --- | --- | --- | --- |
| A: captured `eb`/`cb` → captured `s3_l` | all | exact (8/8) | — |
| B: Python partition sums → `s3_l` | `ecb`, relative | 1.69e−3 | masker, call 3, p43 |
| B | `ctb`, relative | 2.75e−3 | masker, call 3, p50 |
| B | ratio, relative | 2.80e−3 | masker, call 3, p50 |
| B | tonality input, absolute | 2.80e−3 | masker, call 3, p50 |
| B at the retained case | `ctb` and ratio, relative | 1.13e−3 | burst, call 4, p50 |

**Isolating the retained case** (burst call 4): replacing only input partition 50's captured `cb` (257745504) with the Python value (257107760, −0.247%):
- `ecb` is unchanged.
- `ctb` changes in exactly the output partitions 47–62, the nonzero rows of `s3_l` column 50.
- The largest changes are in p50 (1.08e−3 relative in `ctb` and ratio; ratio 0.525961 → 0.525396), p51 (6.1e−4) and p49 (1.6e−4). Everything else is below 6e−5.
- The tonality input moves by the same amounts in absolute terms. In `tbb = −0.299 − 0.43·cbb` that is a change of up to 4.6e−4. Its effect on SNR and the threshold is for the threshold comparison.

The end-to-end tolerance remains open until then. The largest Python-chain differences in this stage come from the established tone (masker, call 3), not from the burst case.

**Implementation (Task 3J, 2026-11-12).** `adapters.dsp_analysis.spreading`:
- `dist10_spreading_matrix()` builds `s3[output][input]` from the embedded 44.1 kHz `bval` of the pinned `psy_data` (SHA-256 cited in the module). It keeps the asymmetric ×3.0/×1.5 slopes, the cutoff on `tempy` before the bump, and `LN_TO_LOG10 = 0.2302585093`.
- `dist10_spread_tonality_input(eb, cb)` returns float32 `ecb` (rounded after every term, ascending input partition), float64 `ctb`, the `divided` mask, the unclamped `ratio`, and `log_ratio`: log of the ratio clamped below at 0.01, or 0.0 where the division is skipped.
- Calls are processed in chunks, and an `ecb` beyond float32 is rejected with `ValueError`.

Against Probe F:
- **Matrix:** same zero pattern; 3,902 of 3,969 entries bit-identical; the rest within 1.8e−16 relative (libm).
- **With the captured matrix:** exact on all 8 calls (log ratio within 1e−15).
- **With the generated matrix:** `ecb` bit-identical (0 ulp), `ctb` within 4.2e−16 relative, log ratio within 4.4e−16.
- **Full Python chain** (report only): worst ratio 2.80e−3 relative and log ratio 2.80e−3 absolute, both at the retained masker call 3, p50. At the retained burst call 4, p50: ratio 1.13e−3, log ratio 1.13e−3.

Overflow and the caller's NumPy policy:
- `ecb` beyond float32, and `ctb` or `ctb/ecb` beyond float64, are rejected with `ValueError` naming the first call and output partition.
- With the dist10 matrix (largest entry about 1.0), `ctb` and the ratio cannot overflow: `ctb` is at most 63 × float32 max ≈ 2.1e40, and the ratio at most that over the smallest nonzero float32 `ecb`, about 1.5e85. Only a supplied matrix with large entries reaches these checks.
- Underflow in the float32 `ecb` store (rounding to a subnormal or to zero, as the reference does) is not an error. So a raising NumPy policy no longer changes the result.

Testing the cutoff after adding the bump would give the same 44.1 kHz matrix. The bump is nonzero only where `tempy` lies between about −1.3 and −16.8, far from −60. So the order is fixed by the source, not by the fixture.

### 3.5 Tonality, SNR and partition thresholds (probe G, Task 3K, 2026-11-16)

**Probe G** is a seventh observation-only patch, built on probe F (`capture/l3psy_threshold_probe.diff` from `capture/make_probe_threshold.py`, `capture/harness_threshold.c`, `capture/run_threshold_in_container.sh`; outputs `expected_threshold/`). It records:
- in the tonality loop: `tbb` after `tbb = −0.299 − 0.43·cbb` (raw), after the [0, 1] clamp, and `SNR_l[b]`;
- after the `nb` loop: `nb`, the history `nb_1`/`nb_2` as read, and the tables `minval`, `qthr_l` and `norm_l` (stored once, in `tables_44100.json`, with the `psy_data` strings);
- in the pre-echo loop: `temp_1` and `thr[b]`, the final partition threshold before band mapping; after the loop, the history as stored.

It also repeats probe F's `ecb` and tonality input on its own calls, so every call can be reconstructed. Calls: every call of the 1 kHz tone at 8192 from start-up to established history (calls 0–5; call 3 is the named masker case) and every call of the burst (calls 0–3 silent, call 4 the onset and named case, 5–7). Only the G fields are stored (144 KB). Every input's output, with the E, F and G fields removed, was byte-identical to its stored D, C or B file. The E and F fields equalled `expected_partition` and `expected_spreading`, and the window, mapping and matrix files equalled the stored ones. So the earlier probes are unchanged.

**Tables.** `L3para_read` reads the 44.1 kHz long block of `tables/psy_data` (block `freq = 44100.0 partition = 62`, rows No = 0…62) with `fscanf("… minval=%lf qthr=%lf norm=%lf bval=%lf")` into `static double` arrays (l. 784). Nothing changes them afterwards: the only other assignment to `minval` (ll. 845 and 927) sits under `#ifdef DEBUGP`, which the build does not define. So the values are the doubles nearest the decimal text:
- `minval` (dB): 1 decimal; 24.5 (p0–4), 20 (p5–11), 18 (p12–14), 12 (p15), 6 (p16–17), 3 (p18–21), 0 (p22–62);
- `qthr` (threshold in quiet, unnormalised FFT energy of int16 counts): 3 decimals, 0.009 to 660.124;
- `norm`: 3 decimals, 0.475 to 0.951.

The captured arrays equal `float()` of the `psy_data` strings exactly. They are table data from the ISO distribution; their derivation is not traced here [U].

**Traced source behaviour, checked against the capture** (l3psy.c, original lines; `maximum(x, y) = x > y ? x : y`, `minimum(x, y) = x < y ? x : y`, ll. 33–34):

| Step | Source | Precision | Capture evidence |
| --- | --- | --- | --- |
| 1. Raw tonality | `tbb = −0.299 − 0.43·cbb`, with `cbb` the tonality input of §3.4 (l. 602) | double | exact, 882 of 882 partition values (14 calls × 63) |
| 2. Clamp | `tbb = minimum(1.0, maximum(0.0, tbb))` (l. 603) | double | exact. Both ends and the interior occur: `tbb = 1` on 45–48 partitions in the silent burst windows and at tone call 0; `tbb = 0` on 49–55 partitions in the other active calls |
| 3. SNR | `SNR_l[b] = maximum(minval[b], 29·tbb + 6·(1 − tbb))` (l. 604; TMN = 29, NMT = 6 dB) | double | exact. `minval` decides 5–16 partitions per call, the tonality term the rest. Where `tbb = 0` and `minval = 6` (p16–17) the two are equal, and the macro returns the tonality term, which has the same value |
| 4. Unbounded threshold | `nb[b] = ecb[b]·norm_l[b]·exp(−SNR_l[b]·LN_TO_LOG10)` (l. 608), with the truncated `LN_TO_LOG10 = 0.2302585093` | `ecb` float32 widened, double product, **`nb` stored as float32** (`FLOAT*` work array) | reconstructed with Python's `math.exp`: bit-identical on 882 of 882 |
| 5. Pre-echo limit | `temp_1 = minimum(nb[b], minimum(2.0·nb_1[chn][b], 16.0·nb_2[chn][b]))` (l. 613) | double | exact |
| 6. Final threshold | `thr[b] = maximum(qthr_l[b], temp_1)` (l. 614). The quiet floor is applied **after** the limit | double | exact |
| 7. History | read in steps 5–6, then `nb_2[chn][b] = nb_1[chn][b]; nb_1[chn][b] = nb[b]` (ll. 615–616), inside the same loop after `thr[b]`. **The stored value is the unbounded float32 `nb`**, not `temp_1` or `thr`. `nb_1`/`nb_2` are `static double` (l. 75) with static zero initialisation and no reset | double holding a float32 value | `nb_1`/`nb_2` read at call 0 are all zero; at call k they equal the `nb` of calls k − 1 and k − 2; each call stores `(nb, previous nb_1)` |

The history update sits before the block-switching branch (l. 632), so it runs on short-path calls too (tone calls 0–1 and burst calls 4–5, `pe ≥ 1800`); the capture confirms this. With zero history, `temp_1 = minimum(nb, 0) = 0` at calls 0 and 1, so `thr = qthr` there whatever the signal. At call 0 the inner `minimum(0, 0)` is a tie of equal values.

**Which term sets `thr`** (strict winner per partition; no ties occurred):

| Input | Calls | qthr | nb | 2·nb₋₁ | 16·nb₋₂ |
| --- | --- | --- | --- | --- | --- |
| tone | 0–1 (zero history) | 63 | — | — | — |
| tone | 2 | 1 | 50 | — | 12 |
| tone | 3 | 1 | 62 | — | — |
| tone | 4–5 | 3 | 60 | — | — |
| burst | 0–3 (silent window) | 63 | — | — | — |
| burst | 4 (onset) and 5 | 63 | — | — | — |
| burst | 6 | 2 | 59 | 2 | — |
| burst | 7 | 1 | 59 | 3 | — |

At burst calls 4–5 the history comes from silent windows, so the pre-echo limit falls far below `qthr` and threshold in quiet decides every partition (as §3 reported at band level). Burst call 5 and tone call 5 analyse the same window (the burst is the tone with zeros before sample 2000): `ecb`, `nb` and everything up to step 4 are identical, but `thr` differs, set by `qthr` for the burst and by `nb` for the tone. This is the history effect isolated by the data.

**Named cases** (both partition 50: `minval` 0, `qthr` 0.18, `norm` 0.489):
- Masker (tone) call 3: tonality input −0.5459, raw `tbb` −0.0643, clamped to 0, SNR 6 dB, `nb` = 58.46. Pre-echo terms 2·nb₋₁ = 115.5 and 16·nb₋₂ = 7.26e6, so `thr = nb = 58.46`.
- Burst call 4: tonality input −0.6425, raw `tbb` −0.0227, clamped to 0, SNR 6 dB, `nb` = 1.386e8. The silent history gives 2·nb₋₁ = 1.26e−5 and 16·nb₋₂ = 1.00e−4, so `temp_1` = 1.26e−5 and `thr = qthr = 0.18`.

**Wrong variants the verifier rejects** (`verify_fixture.py` §7). Each changes `thr` on at least one captured call:

| Variant | Calls where `thr` changes (partitions) |
| --- | --- |
| Swapped history ages (2·nb₋₂, 16·nb₋₁) | tone call 2 (14); burst calls 6 (4) and 7 (6) |
| Final `thr` stored in history (replayed from call 0) | from tone call 2 and burst call 4 on (53–63 per call) |
| Quiet floor before the limit (`min(max(qthr, nb), 2·nb₋₁, 16·nb₋₂)`) | 63 on every call with zero or silent history (tone 0–1, burst 0–5); 0–2 once history is established (tone 2–5, burst 6–7) |

The verifier also rejects stored records whose history ages are swapped, whose stored history is `thr`, or whose `thr` follows the floor-first order. The evidence for swapped ages is thin: only three calls, because `16·nb₋₂` and `2·nb₋₁` each decide only at the end of start-up.

**Comparisons** (`capture/compare_threshold_inputs.py`):
- **A: captured inputs and history → thresholds:** exact on all 14 calls (steps 1–3 and 5–7 exact; `nb` bit-identical).
- **B: Python chain** (spectra, `cw`, partition sums, spreading) on every call from 0, through the same arithmetic, with the history replayed from zero using the Python `nb`. Worst residuals:

| Stage | Worst | Where (input, call, partition) |
| --- | --- | --- |
| `ecb`, relative | 2.81e−3 | burst, call 5, p40 |
| tonality input, absolute | 2.80e−3 | tone, call 3, p50 |
| raw `tbb`, absolute | 1.20e−3 | tone, call 3, p50 |
| clamped `tbb`, absolute | 5.80e−4 | burst, call 5, p47 |
| SNR, absolute (dB) | 1.33e−2 | burst, call 5, p47 |
| `nb`, relative | 3.32e−3 | burst, call 5, p47 |
| `temp_1` and `thr`, relative | 3.32e−3 | tone, call 5, p47 (the same window as burst call 5) |

- **Decisions:** no difference changes which term sets `thr`, which side of the clamp `tbb` falls on, or whether `minval` or the tonality term sets the SNR (0 of 882 each).
- **Margins in the captures:** `thr`'s winner leads its runner-up by at least 1.28e−2 relative (tone, call 2, p62), about 4 times the worst `thr` residual. The raw `tbb` comes within 4.6e−4 of a clamp end (burst, call 4, p33, raw 4.59e−4), less than the worst raw-`tbb` residual (1.2e−3) elsewhere. At that partition the Python residual is only 1.3e−6, so the side does not change here. A clamp-side flip is therefore possible on other inputs.
- **Named cases:** at tone call 3, p50 the tonality input moves by 2.80e−3, but `tbb` is clamped to 0 on both sides, so the SNR is identical and `thr` moves only with `ecb` (4.78e−5 relative). At burst call 4, p50 (the Task 3G `cb` case) the tonality input moves by 1.13e−3; the clamp absorbs it again, `nb` moves by 1.15e−7 relative, and `thr = qthr` exactly on both sides. The partition-50 difference therefore does not reach the threshold at either named case.

**Not covered by the captures:**
- the zero-energy branch (`ecb = 0`, so `cbb = 0`, raw `tbb` = −0.299 and SNR from 6 dB): unreachable for profile v1 (§3.4);
- ties between `qthr` and `temp_1`, and between nonzero pre-echo terms (`nb` = 2·nb₋₁, 2·nb₋₁ = 16·nb₋₂): none occur; the macros' tie rules return equal values, so a tie cannot change `thr`, only the label of the deciding term;
- the short-block threshold path (l. 693, which uses `norm_l` and `+SNR_s`): out of scope for the long-block contract;
- stereo history (`chn = 1`) and other rates.

The end-to-end tolerance stays open until the effect on band thresholds (`thm`) and masking margins is measured. The Python threshold kernel and the scale-factor-band mapping are later tasks.

**Implementation (Task 3L, 2026-11-16).** `adapters.dsp_analysis.thresholds.dist10_partition_thresholds(ecb, log_ratio)` takes float32 `ecb` and float64 `log_ratio` (`SpreadTonalityInput.ecb` and `.log_ratio`), `(calls, 63)` in call order, for one clip. It returns `PartitionThresholds`: `tonality` (clamped `tbb`), `snr`, float32 `nb`, `pre_echo_limit` and `thr`, all read-only. This supersedes the sentence above for the kernel; band mapping remains a later task.
- The `minval`, `qthr` and `norm` tables are embedded as the decimal values of the pinned `psy_data`, with its SHA-256 cited.
- The operation order, dist10's macro tie rules, the float32 `nb` store and the quiet floor after the limit follow steps 1–7.
- The history starts at zero for every invocation and every call is processed. Because `nb` does not depend on the history, the limit is formed from `nb` shifted by one and two calls. Calls are processed in chunks of 1,024, with the last two `nb` rows carried across chunks.
- No value can overflow (`nb ≤ ecb`), and float32 underflow is not an error under any NumPy policy.

Against Probe G, with each clip replayed from call 0 using the captured spread inputs, every output is exact on all 14 calls, `nb` included (NumPy `exp`). With the full Python chain (report only), the worst `thr` difference is 3.32e−3 relative (tone, call 5, p47) and no quiet-floor decision changes. At the named cases, `thr` is 58.4603 → 58.4631 (4.78e−5) at tone call 3, p50 and 0.18 on both sides at burst call 4, p50.

### 3.6 Long scale-factor-band mapping and directional margins (probe H, Task 3M, 2026-11-16)

**Tables.** After the partition tables, `L3para_read` reads one block per rate with `fscanf("freq=%lf sb=%d")` and then, per row, `fscanf("sb=%d cbw=%d bu=%d bo=%d w1=%lf w2=%lf")` into `cbw_l`, `bu_l`, `bo_l` (int) and `w1_l`, `w2_l` (static double) (ll. 938–968). At 44.1 kHz the block is `freq=44100.0 sb=20` (rows sb = 0…20; `psy_data` l. 650), with weights written to 3 decimals. From sb 1 on, the source rejects a row unless `bo = bu + cbw` and `|1 − w1[sb] − w2[sb − 1]| ≤ 0.01`. Row sb 0 is not checked, and it is the one row that breaks the first relation (`cbw` 3, `bu` 0, `bo` 4).

| sb | bu | bo | w1 | w2 | sb | bu | bo | w1 | w2 | sb | bu | bo | w1 | w2 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0 | 4 | 1.000 | 0.056 | 7 | 21 | 24 | 0.417 | 0.250 | 14 | 43 | 46 | 0.532 | 0.623 |
| 1 | 4 | 7 | 0.944 | 0.611 | 8 | 24 | 27 | 0.750 | 0.805 | 15 | 46 | 49 | 0.376 | 0.450 |
| 2 | 7 | 11 | 0.389 | 0.167 | 9 | 27 | 30 | 0.194 | 0.574 | 16 | 49 | 52 | 0.550 | 0.552 |
| 3 | 11 | 14 | 0.833 | 0.722 | 10 | 30 | 33 | 0.426 | 0.537 | 17 | 52 | 55 | 0.448 | 0.403 |
| 4 | 14 | 17 | 0.278 | 0.139 | 11 | 33 | 36 | 0.463 | 0.819 | 18 | 55 | 57 | 0.597 | 0.643 |
| 5 | 17 | 18 | 0.861 | 0.917 | 12 | 36 | 40 | 0.180 | 0.100 | 19 | 57 | 59 | 0.357 | 0.722 |
| 6 | 18 | 21 | 0.083 | 0.583 | 13 | 40 | 43 | 0.900 | 0.468 | 20 | 59 | 61 | 0.278 | 0.960 |

The 21 bands cover partitions 0–61 contiguously. Each endpoint partition `bo[sb] = bu[sb + 1]` is shared: band sb takes `w2[sb]` of it and band sb + 1 takes `w1[sb + 1]`. The two weights sum to 1 within the 0.01 check (exactly 1.000 in most rows). Partition 62, the top 18 lines of the long table, is never read by the long bands. Band 5 has no interior partition. The weights are table data. Their derivation, presumably the share of the endpoint partition that lies inside the band, is not traced [U].

**Operation (ll. 652–660; probe B applies it on every call).** For sb = 0…20:
1. `en[sb] = w1_l[sb]·eb[bu_l[sb]] + w2_l[sb]·eb[bo_l[sb]]` (two double products, then their sum);
2. `en[sb] += eb[b]` for b = bu + 1 … bo − 1, ascending;
3. `thm[sb]` the same with `thr`.

`eb` is the static double partition energy of §3.3. `thr` is the local double final threshold of §3.5 step 6, after the pre-echo limit and the quiet floor. The encoder runs the mapping only when `pe < 1800`. Probe B applies the same expressions after `pe` and before the block-switching branch, so the fixed long-band mapping is defined on every call, including the encoder's short-path calls (§2.2 option (b)). `ratio = thm/en` and the short-block bands are not part of the contract.

**Probe H** is an eighth observation-only patch, built on probe G (`capture/l3psy_band_probe.diff` from `capture/make_probe_band.py`, `capture/harness_band.c`, `capture/run_band_in_container.sh`; outputs `expected_band_inputs/`). Right before probe B's mapping, it copies `eb[0..62]` and `thr[0..62]` as the mapping reads them, and the band tables (stored once, in `bands_44100.json`, with the `psy_data` strings). It runs on every call of all nine inputs: 92 calls, 13 of them short-path calls, 204 KB stored.
- **Capture:** from a fresh extraction, with the B–G diffs regenerated identically. Two runs were byte-identical in outputs and binary.
- **Earlier probes unchanged:** every output, with the E–H fields removed, equals its stored D, C or B file. The E, F and G fields equal the stored captures, and the window, mapping, matrix and tables files are unchanged.
- **Inputs:** `band_eb` equals probe E's `eb` and `band_thr` equals probe G's `thr` wherever those probes captured the call.

**Reconstruction from captured inputs** (`verify_fixture.py` §8):
- The captured tables equal the `psy_data` rows exactly, and `band_thr ≥ qthr` everywhere.
- `en`, `thm` and `thm_quiet` reconstruct probe B **exactly on all 92 calls**, short-path calls included, from `band_eb`, `band_thr` and `qthr_l`.
- Wrong variants are rejected on every call, with both `en` and `thm` changing: swapped endpoint weights, and the interior range moved by one partition at either end (starting at `bu` or `bu + 2`, ending at `bo` or `bo − 2`).
- Summing the interior before the endpoint terms changes `en` on all 92 calls and `thm` on 91, so the summation order is observable and fixed by the capture.

**Python chain** (`capture/compare_band_margins.py`): framing, spectra, `cw`, partition sums, spreading and the threshold kernel, with each clip replayed from call 0, then the same mapping. Against probe B, on every call of the nine inputs:

| Quantity | Worst absolute | Worst relative |
| --- | --- | --- |
| `en` | 5.23e5 (burst, call 7, band 6; value 6.13e12) | 1.28e−3 (tone 8192, call 9, band 12; 403.25 → 403.77) |
| `thm` | 1.34e5 (burst, call 7, band 6; value 1.56e12) | 1.05e−3 (tone 8192, call 5, band 15; 167.17 → 166.99) |
| `en`, short-path calls only | 5.21e5 (burst, call 5, band 6) | 1.16e−3 (burst, call 5, band 0) |
| `thm`, short-path calls only | 0 | 0 (`thr = qthr` on every short-path call) |

**Partition 50** is interior to band 16 only (bu 49, bo 52; weight 1):
- **Tone call 3:**
  - `eb[50]` 280.574 → 280.595 (7.2e−5) gives band-16 `en` 1083.10 → 1083.41 (2.9e−4); p50 is 26% of that band's energy.
  - `thr[50]` 58.4603 → 58.4631 (4.8e−5) gives band-16 `thm` 243.728 → 243.770 (1.7e−4); p50 is 24% of it.
  - In both, the band difference comes mostly from the other partitions.
- **Burst call 4** (an encoder short-path call):
  - `eb[50]` 5.18e8 (5.5e−7) gives band-16 `en` 1.6577e9 (1.6e−7).
  - `thr[50]` = `qthr` = 0.18 on both sides, so band-16 `thm` = 0.6978 = `thm_quiet` on both sides, exactly.

**Directional margins** (approved kernel `directional_masking_margin_db`; `T` = masker `thm`, `E` = target `en`; valid frames k = 2…11 of §2.2, 10 × 21 cells per direction; history replayed from call 0 for each clip):

| Direction | Margin range (probe B) | Cells `T > E` (B / Python) | Largest error | Where | `T` and `E` (probe B → Python) |
| --- | --- | --- | --- | --- | --- |
| tone 8192 → target 1024 | −45.3 … 95.8 dB | 70 / 70 | 4.67e−3 dB | frame 5, band 15 | `T` 167.167 → 166.991; `E` 880.450 → 880.470 |
| target 1024 → tone 8192 | −108.0 … 64.9 dB | 60 / 60 | 5.55e−3 dB | frame 9, band 12 | `T` 1.73664e7 → 1.73664e7; `E` 403.250 → 403.766 |
| tone 81.92 → target 10.24 | −41.6 … 69.5 dB | 50 / 50 | 4.16e−4 dB | frame 11, band 12 | `T` 9.45838 → 9.45747; `E` 178.4349 → 178.4348 |
| target 10.24 → tone 81.92 | −83.7 … 35.0 dB | 39 / 39 | 2.35e−4 dB | frame 11, band 2 | `T` 0.0985959 → 0.0985959; `E` 29.4635 → 29.4619 |

- **No sign changes** in any direction. The cell nearest 0 dB lies at −1.66, +1.80, −1.13 and −1.36 dB respectively, far outside the errors. So the captures do not test a cell within the errors of 0 dB.
- **Exact-silence controls**, reported separately:

  | Control | Margin range | Cells `T > E` | Error |
  | --- | --- | --- | --- |
  | silence → target 1024 | −120.6 … −5.9 dB | 0 | ≤ 1.3e−3 dB |
  | silence → target 10.24 | −80.6 … −7.2 dB | 0 | ≤ 6.4e−5 dB |
  | tone 8192 → silence | 28.7 … 147.7 dB | all 210 | ≤ 4.6e−3 dB |
  | silence → silence | 12.6 … 40.7 dB | all 210 | 0 |

  Where the target is silent, every cell "exceeds", because `E` is the FFT energy floor. The silent-window flag (no nonzero int16 sample in `[576k − 768, 576k + 256)`) marks exactly those frames: all 10 valid frames when the target is silent, none for the active targets. The Python flag computed from the raw input equals probe B's on every valid frame. A margin on a flagged frame can therefore be identified as floor-driven and must not be read as target activity.

**Coverage gaps:**
- The signal-derived `thr` never decides on a short-path call: all 13 have zero or silent history, so short-path `thm` is tested only at `qthr`.
- No captured margin lies within the errors of 0 dB.
- No stereo, no other rate, and only the Layer III long band table.
- The derivation of `w1`/`w2` is not traced.

The end-to-end tolerance remains open for review. The production band-mapping kernel follows after review.

**Implementation (Task 3N, 2026-11-16).** `adapters.dsp_analysis.bands.dist10_long_bands(eb, thr)` takes aligned float64 `eb` and final `thr`, each `(calls, 63)`, and returns `LongBandValues`: `en` and `thm`, fresh read-only float64 arrays `(calls, 21)`. This supersedes the last sentence above for the kernel; the tolerance stays open.
- The `bu`, `bo`, `w1` and `w2` rows of the pinned `psy_data` are embedded, with its SHA-256 cited. `bu` and `bo` are used as recorded, so band 0 ends at partition 4. Partition 62 is never read.
- For each band the two weighted endpoints are summed first, then the interior partitions in ascending order. The same mapping is applied to `eb` and `thr`.
- Every call is mapped in order, start-up and short-path calls included; eligibility and exact silence are the caller's concern.
- Malformed inputs, mismatched call counts, negative or non-finite values and a mapped sum beyond float64 are rejected; the overflow error names the quantity, call and band. The empty result has shape `(0, 21)`.

With Probe H's captured `eb`/`thr` the kernel reproduces Probe B's `en`, `thm` and `thm_quiet` exactly on all 92 calls. Used for the Python chain in `capture/compare_band_margins.py`, it equals the verifier's independent mapping on every call and leaves the comparison output unchanged: on the 840 eligible tone-pair cells the largest margin difference is 5.552e−3 dB, with no sign change. This is descriptive only. Analyzer integration and the interpretation of masking margins are later tasks.

**Composed front end (Task 3O, 2026-11-16).** `adapters.dsp_analysis.front_end.dist10_clip_to_bands(samples, *, sample_rate_hz) -> ClipBandAnalysis` takes one mono int16 clip at 44,100 Hz.
- **Validation and framing:** inputs are validated by `frame_long_blocks`.
- **Order:** it composes the reviewed kernels in the reference's order: framing, long and short spectra, unpredictability, partition sums, spreading, partition thresholds and the long-band mapping.
- **Calls:** every call is processed, including the start-up calls whose history the predictor and the threshold stage need.
- **Result:** `sample_count`, `sample_rate_hz`, the framer's `call`, `window_start`, `window_end`, `eligible` and `nonzero_samples`, and read-only float64 `en`/`thm` `(calls, 21)`. Ineligible and exactly silent calls (`nonzero_samples == 0`) stay in the result with their metadata. Windows and intermediate spectra are not kept. An empty clip gives zero calls.

The entry point equals the direct composition of the kernels on all nine fixture clips, and its metadata equals Probe B's on all 92 calls. On the silence clip its `en` and `thm` equal Probe B exactly. The comparison script now takes its Python path from the entry point and reports the same evidence as before (840 eligible tone-pair cells, largest margin difference 5.552e−3 dB, no sign change; descriptive). WAV conversion, analyzer integration and interpreting the margins are later tasks.

## 4. Remaining implementation decisions and open questions

1. **Block-switching option** (a) or (b) (§2.2). Under (b), decide whether onset frames whose threshold is capped by pre-echo control need their own label in the evidence.
2. **Threshold wording**: confirm with the owner that dist10's `max(qthr, min(…))` is acceptable as the backend reading of the thesis's "maximum of three values". Otherwise specify an alternative and its source.
3. **Band table**: Layer III long bands (this draft) versus AAC bands. This needs the author code or a statement from the authors.
4. **Target-activity gate and action thresholds**: separate decision. The front end supplies the exact silent-window flag, `thm_quiet` and the quiet share; it does not gate.
5. **Reimplementation tolerance** against the float32 reference, set from fixture residuals in the production increment. Decide whether the Python FFT kernel imitates float32 storage (stage-wise rounding, float32 floor test) or uses float64 and accepts a peak-relative tolerance. The floor decision is sensitive to this near 0.0005: bins 169–170 of the quiet target are well below the floor, but a bin within float32 rounding of 0.0005 could flip.

Task 3C (2026-11-12) took the following steps for the long window, as specified by the owner:
- window and windowed samples in float32;
- an unnormalised float64 FFT;
- pre-floor energy rounded to float32 before the floor test;
- the floor replacement stored as float32.

Against Probe C, the energy error is at most 3.1e−7 of the call's peak, and the floor masks are identical on all nine captured calls. The nearest captured nonzero energy is 1.29e−4 away from the cutoff, so the capture does not test bins within float32 rounding of 0.0005. A reimplementation tolerance for later stages is still open.

Task 3E (2026-11-12) applies the same strategy to the short window (`adapters.dsp_analysis.short_spectrum`):
- blocks at long-window positions 256, 384 and 512;
- float32 window and products;
- a float64 FFT;
- float32 pre-floor energy, with the floor on bins 0–127 and phase zeroed there.

Against all 48 Probe D blocks: the energy error is at most 1.93e−7 of the block's peak, and the phase error at most 3.74e−5 rad on 1,021 strong unfloored bins. The floor masks are identical, including exactly bins 42 and 86 in the quiet target's call 6, block 2. The nearest captured nonzero energy is 3.7e−4 below the cutoff.

Task 3K (2026-11-16, §3.5): through the partition thresholds, the Python chain's worst residual is 3.3e−3 relative in `thr`, and no decision changes (deciding term, clamp side, SNR term). Two decisions are sensitive in principle: the raw `tbb` can lie closer to a clamp end (4.6e−4 observed) than the chain's worst raw-`tbb` residual (1.2e−3), and the `thr` winner's lead (1.3e−2 at least) is only about 4 times the worst `thr` residual. A tolerance therefore cannot be stated per stage alone; it waits for the band-threshold and margin comparison [U].
6. **Deferred profile items**: float-WAV conversion, stereo, 48 kHz (needs a fixture), multi-masker sums (sum domain and clipping).
7. **Time aggregation** of per-frame margins: the thesis states none; Eq. 7.13 is per band. Out of scope here.
8. Retry the SoundSoftware author repository; if it is reachable, compare its threshold and band code with dist10.
