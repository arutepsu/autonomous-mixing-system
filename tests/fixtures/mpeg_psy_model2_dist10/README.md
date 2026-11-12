# dist10 psychoacoustic model 2 reference fixture

Reference values from ISO dist10 `L3psycho_anal`, our chosen backend for the MPEG model in Ronan's thesis (Ch. 7). They cover the long-block scale-factor-band energy `en[sb]` and threshold `thm[sb]` (21 bands), and the long-window FFT stage behind them. They are for verifying a future front end. Contract and source trace: `docs/superpowers/specs/2026-11-11-mpeg-psychoacoustic-front-end-contract.md` (FFT stage: §3.1).

The inputs are synthetic numerical test signals (tones, impulses, silence), not musical material or evaluation data. The fixture shows only what dist10 computes. It does not show that the model is perceptually valid or that it matches Ronan's implementation.

## Five separately labelled captures

| Folder | Probe | What it is |
| --- | --- | --- |
| `expected/` | **A, encoder-path capture** (`capture/l3psy_probe.diff`, `capture/harness.c`) | `en`/`thm` exactly where the encoder computes them; `null` on short-block calls. Only clips that are whole 1152-sample frames. |
| `expected_long_mapping/` | **B, long-band mapping probe** (`capture/l3psy_mapping_probe.diff`, `capture/harness_mapping.c`) | Project extension, not encoder behaviour. The encoder's long-band mapping applied on every call to the long-block partition values before the block-switching branch. Equal to A bit for bit on long-path calls. |
| `expected_long_fft/` | **C, long-FFT probe** (`capture/l3psy_subs_fft_probe.diff`, `capture/harness_fft.c`; built on B) | Observation only. For selected calls: the windowed samples passed into the long FFT, and the per-bin energy before and after the 0.0005 floor and the phase, as `L3psycho_anal` receives them. Also `window_long.json`, the Hann coefficients. Every line also carries B's fields, unchanged. |
| `expected_short/` | **D, short-FFT / unpredictability probe** (`capture/l3psy_subs_short_probe.diff`, `capture/harness_short.c`; built on C) | Observation only. For consecutive selected calls: the three short-FFT inputs, the short energy before and after the floor and the phase (bins 0–128), the long-predictor history for bins 0–5, and `cw[0..512]` before partitioning. Also `window_short.json`, the short Hann coefficients. With the D fields removed, every line equals the stored C (or B) line. |
| `expected_partition/` | **E, partition probe** (`capture/l3psy_partition_probe.diff`, `capture/harness_partition.c`; built on D) | Observation only. For selected probe D calls: `partition_energy` (513, float32) and `partition_cw` (513, double) as read by the long partition loop, and `eb` (63, double) and `cb` (63, float32) right after accumulation, before spreading. Calls: silence 0–2, the 1983 impulse 3–4, the burst 4, the 8192 masker 3 and the quiet target 6. Only these fields are stored; the full outputs, with them removed, equalled the stored D/C/B files. Also `mapping_44100.json`: `partition_l`, the `numlines` at partition time, and the 44.1 kHz long and short `#lines` of `psy_data`. |

Probe D line fields (selected calls; contract §3.2):
- `short_windowed_samples`: 3 × 256 float32. Block s position j is stream sample 576k − 512 + 128s + j.
- `short_energy_prefloor`, `short_energy` and `short_phi`: 3 × 129 float32, natural short bins 0–128.
- `long_history`: `age_new`, `age_old` and `age_oldest`; `r_old`, `r_oldest`, `phi_old` and `phi_oldest` (6 float32 each, read before the long predictor loop); `r_new` and `phi_new` (stored by this call).
- `cw`: 513 doubles. Lines 0–5 come from the long predictor, 6–205 from short bin (j + 2)/4 in groups of four, and 206–512 are 0.4.

The probe D calls are:
- silence: calls 0–2;
- the 1983 impulse: calls 2–5;
- the burst: calls 3–5;
- the 1 kHz masker at 8192: calls 0–4;
- the quiet 2 kHz target at 10.24: call 6. This is the partially floored active short block: block 2 floors exactly short bins 42 and 86. Their pre-floor energy is at most 1.27e−4; every other bin 0–127 has at least 2.06e−2, and Nyquist has 42.4.

The other four inputs gave output byte-identical to the stored C/B files.

Probe A line fields: `call`, `pe`, `block_type_reported` (the encoder's previous decision), `long_path`, `returned_ratio_l` (the previous call's ratio), `en`, `thm`.

Probe B line fields:
- `call`, `pe` and `encoder_long_path` (whether `pe < 1800`).
- `clip_samples`, `window_start`, `window_end` and `window_end_within_clip`.
- `window_nonzero_samples`: the nonzero int16 samples in the analysed window. 0 means exact digital silence.
- 21 values each of `en`, `thm`, `thm_quiet` (band mapping of threshold in quiet), `thm_from_quiet_partitions` (the part of `thm` from partitions where threshold in quiet decided) and `thm_signal` (band mapping of `min(nb, 2·nb₋₁, 16·nb₋₂)`).

Probe C line fields: probe B's fields. Selected calls also carry four arrays of 1024 float32 values each, printed with `%.9g` (an exact float32 round trip):
- `windowed_samples`: indexed by window position j = stream sample 576k − 768 + j.
- `energy_prefloor`, `energy` and `phi`: indexed by natural FFT bin k (0 = DC, 512 = Nyquist; 513–1023 mirror 511–1 and are never read by the encoder).

The selected calls are:
- silence: call 2;
- impulses at 1983 and 1984: calls 3 and 4;
- the four level-case tones: call 6.

The burst and truncated inputs have no selected calls. Their probe C output was byte-identical to probe B's and is not stored again; `provenance.json` records the hashes.

Call `k` analyses stream samples `[576k − 768, 576k + 256)`, with zeros before sample 0. Probes B and C fill a final partial granule with zeros, as the dist10 encoder does.

## Contents

- `inputs/*.raw`: mono int16 little-endian, 44,100 Hz. Formulas and SHA-256 are in `inputs/inputs.json`. All inputs are whole granules except `target_2k_a1024_trunc6536`, which is the final-frame case.
- `provenance.json`: archive URL, commit and SHA-256, and hashes of every source and table used. Also every probe patch, patched source, harness source and binary, the container digest, compiler and flags, and the input and output hashes.
- `capture/`:
  - `make_inputs.py`: the signal generator.
  - `make_probe.py`, `make_probe_mapping.py`, `make_probe_fft.py`, `make_probe_short.py` and `make_probe_partition.py`: generate the patches.
  - The five harnesses, plus `run_in_container.sh` (A and B), `run_fft_in_container.sh` (C), `run_short_in_container.sh` (D) and `run_partition_in_container.sh` (E).
  - `verify_fixture.py`: checks hashes, the relations between the probes, the long- and short-FFT evidence and the partition inputs.
  - `compare_partition_inputs.py`: prints the eb/cb residuals of the Python spectra and predictor against probe E (contract §3.3).
  - `summarize_controls.py`: prints the control-case numbers quoted in the contract.
- `.gitattributes`: `-text`, so checkout keeps the hashed bytes exact.

## Verify

```bash
uv run python tests/fixtures/mpeg_psy_model2_dist10/capture/verify_fixture.py
```

This regenerates the inputs and checks every recorded hash. It also checks these relations, exactly unless stated:
- Probe B equals probe A on long-path calls.
- The final-frame clip equals the full clip exactly on the calls whose window ends inside it.
- The silent masker's threshold is threshold in quiet alone.
- Floor energy coincides with a silent window.
- Probe C leaves probe B's values unchanged.
- The window coefficients equal float32 of the dist10 formula.
- The windowed samples equal window × the frame rebuilt from the raw input.
- Against an independent float64 FFT, within float32 tolerances: energy is the unnormalised |X|², Parseval holds, phase follows the forward-transform convention, and an impulse gives v² in every bin.
- The floor hits exactly the natural bins k < 512 with energy ≤ 0.0005.
- Probe D leaves the probe C and B lines unchanged.
- The short window and the short-block samples (exact offsets from the raw input) match.
- The short FFT follows the same conventions, with its floor on short bins 0–127.
- The long-predictor history follows the two-slot age order and starts from zero.
- `cw` reconstructs within 1e−12, using the short-bin mapping, the predictor block order and the constant 0.4.
- The zero-denominator branch is never taken.
- In the partially floored short block, the floored bins are exact, take float32(0.0005) with phase 0, and Nyquist is excluded.
- Probe E leaves the probe D, C and B outputs unchanged (through recorded hashes of the stripped output).
- The partition mapping follows the long `psy_data` table (465 bins), with bins 465–512 in partition 0, and the short table overwrites `numlines[0..38]`.
- The partition inputs equal probe C's energy and probe D's `cw`.
- `eb` (double) and `cb` (float32 store) reconstruct exactly. A double-only `cb` or boundaries moved by one bin would not.

It does not rerun dist10.

## Recapture

This needs Docker and the pinned archive. Download the archive into an empty directory of its own.

1. Extract `dist10/lsf/encoder/` into `build/src/`.
2. Generate the patched sources and compare each printed diff with the recorded one:

   ```bash
   python capture/make_probe.py build/src/l3psy.c build/src/l3psy_probe.c
   ```

   ```bash
   python capture/make_probe_mapping.py build/src/l3psy.c build/src/l3psy_mapping_probe.c
   ```

   ```bash
   python capture/make_probe_fft.py build/src/l3psy_mapping_probe.c build/src/subs.c build/src/l3psy_fft_probe.c build/src/subs_fft_probe.c
   ```

   ```bash
   python capture/make_probe_short.py build/src/l3psy_fft_probe.c build/src/subs_fft_probe.c build/src/l3psy_short_probe.c build/src/subs_short_probe.c
   ```

   ```bash
   python capture/make_probe_partition.py build/src/l3psy_short_probe.c build/src/l3psy_partition_probe.c
   ```

3. Copy the five `capture/harness*.c` files into `build/src/`, and the four `capture/run*_in_container.sh` scripts into `build/`.
4. Generate the inputs into `build/inputs/` with `make_inputs.py`.
5. Run the captures in the pinned container (replace `<build>` with the build directory):

   ```bash
   docker run --rm --network none -v <build>:/w gcc@sha256:d47e63a2a4621ff4da4e0825bfe8c52c2e65e957333d7878e5c3a6eee2cd525e sh /w/run_in_container.sh
   ```

   ```bash
   docker run --rm --network none -v <build>:/w gcc@sha256:d47e63a2a4621ff4da4e0825bfe8c52c2e65e957333d7878e5c3a6eee2cd525e sh /w/run_fft_in_container.sh
   ```

   ```bash
   docker run --rm --network none -v <build>:/w gcc@sha256:d47e63a2a4621ff4da4e0825bfe8c52c2e65e957333d7878e5c3a6eee2cd525e sh /w/run_short_in_container.sh
   ```

   ```bash
   docker run --rm --network none -v <build>:/w gcc@sha256:d47e63a2a4621ff4da4e0825bfe8c52c2e65e957333d7878e5c3a6eee2cd525e sh /w/run_partition_in_container.sh
   ```

6. From `build/out/encoder_path/*.jsonl`, `build/out/long_mapping/*.jsonl`, `build/out/long_fft/*.jsonl` and `build/out/short/*.jsonl`, keep only the lines that start with `{`. Copy `build/out/long_fft/silence_12granules.window.json` as `expected_long_fft/window_long.json` and `build/out/short/silence_12granules.window_s.json` as `expected_short/window_short.json`; within each folder, all nine window files are identical. Keep in `expected_short/` only the five inputs with probe D calls. For probe E, check that each `build/out/partition/*.jsonl` without its four probe E fields equals the stored D, C or B file. Then store per probe E call only `call`, `partition_energy`, `partition_cw`, `eb` and `cb` in `expected_partition/<input>.jsonl`, and write `mapping_44100.json` with `partition_l`, the captured `numlines` and the long and short `#lines` of `tables/psy_data`.

Reproducibility:
- The first capture (probe A) was byte-identical across two runs.
- The recapture from a fresh extraction reproduced probe A's outputs and binary byte for byte.
- Two runs of probes A and B were byte-identical.
- Probe C was captured from another fresh extraction. Its two runs were byte-identical in outputs, window files and binary.
- Probe D was captured the same way, from a further fresh extraction, with two byte-identical runs. Its probe B and C diffs were regenerated and matched the recorded ones.
- Probe E: another fresh extraction, with the B, C and D diffs regenerated identically. Two runs were byte-identical in outputs, window and mapping files and binary.
