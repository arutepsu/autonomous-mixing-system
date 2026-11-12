# dist10 psychoacoustic model 2 reference fixture

Reference values from ISO dist10 `L3psycho_anal`, our chosen backend for the MPEG model in Ronan's thesis (Ch. 7). They cover the long-block scale-factor-band energy `en[sb]` and threshold `thm[sb]` (21 bands), and the long-window FFT stage behind them. They are for verifying a future front end. Contract and source trace: `docs/superpowers/specs/2026-11-11-mpeg-psychoacoustic-front-end-contract.md` (FFT stage: §3.1).

The inputs are synthetic numerical test signals (tones, impulses, silence), not musical material or evaluation data. The fixture shows only what dist10 computes. It does not show that the model is perceptually valid or that it matches Ronan's implementation.

## Three separately labelled captures

| Folder | Probe | What it is |
| --- | --- | --- |
| `expected/` | **A, encoder-path capture** (`capture/l3psy_probe.diff`, `capture/harness.c`) | `en`/`thm` exactly where the encoder computes them; `null` on short-block calls. Only clips that are whole 1152-sample frames. |
| `expected_long_mapping/` | **B, long-band mapping probe** (`capture/l3psy_mapping_probe.diff`, `capture/harness_mapping.c`) | Project extension, not encoder behaviour. The encoder's long-band mapping applied on every call to the long-block partition values before the block-switching branch. Equal to A bit for bit on long-path calls. |
| `expected_long_fft/` | **C, long-FFT probe** (`capture/l3psy_subs_fft_probe.diff`, `capture/harness_fft.c`; built on B) | Observation only. For selected calls: the windowed samples passed into the long FFT, and the per-bin energy before and after the 0.0005 floor and the phase, as `L3psycho_anal` receives them. Also `window_long.json`, the Hann coefficients. Every line also carries B's fields, unchanged. |

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
  - `make_probe.py`, `make_probe_mapping.py` and `make_probe_fft.py`: generate the patches.
  - The three harnesses, plus `run_in_container.sh` (A and B) and `run_fft_in_container.sh` (C).
  - `verify_fixture.py`: checks hashes, the relations between the probes and the FFT-stage evidence.
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

3. Copy the three `capture/harness*.c` files into `build/src/`, and both `capture/run*_in_container.sh` scripts into `build/`.
4. Generate the inputs into `build/inputs/` with `make_inputs.py`.
5. Run the captures in the pinned container (replace `<build>` with the build directory):

   ```bash
   docker run --rm --network none -v <build>:/w gcc@sha256:d47e63a2a4621ff4da4e0825bfe8c52c2e65e957333d7878e5c3a6eee2cd525e sh /w/run_in_container.sh
   ```

   ```bash
   docker run --rm --network none -v <build>:/w gcc@sha256:d47e63a2a4621ff4da4e0825bfe8c52c2e65e957333d7878e5c3a6eee2cd525e sh /w/run_fft_in_container.sh
   ```

6. From `build/out/encoder_path/*.jsonl`, `build/out/long_mapping/*.jsonl` and `build/out/long_fft/*.jsonl`, keep only the lines that start with `{`. Copy `build/out/long_fft/silence_12granules.window.json` as `expected_long_fft/window_long.json`; all nine window files are identical.

Reproducibility:
- The first capture (probe A) was byte-identical across two runs.
- The recapture from a fresh extraction reproduced probe A's outputs and binary byte for byte.
- Two runs of probes A and B were byte-identical.
- Probe C was captured from another fresh extraction. Its two runs were byte-identical in outputs, window files and binary.
