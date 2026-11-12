# dist10 psychoacoustic model 2 reference fixture

Reference values of the long-block scale-factor-band energy `en[sb]` and threshold `thm[sb]` (21 bands) from ISO dist10 `L3psycho_anal`, our chosen backend for the MPEG model in Ronan's thesis (Ch. 7). They are for verifying a future front end. Contract and source trace: `docs/superpowers/specs/2026-11-11-mpeg-psychoacoustic-front-end-contract.md`.

The inputs are synthetic numerical test signals (tones, impulses, silence), not musical material or evaluation data. The fixture shows only what dist10 computes. It does not show that the model is perceptually valid or that it matches Ronan's implementation.

## Two separately labelled captures

| Folder | Probe | What it is |
| --- | --- | --- |
| `expected/` | **A, encoder-path capture** (`capture/l3psy_probe.diff`, `capture/harness.c`) | `en`/`thm` exactly where the encoder computes them; `null` on short-block calls. Only clips that are whole 1152-sample frames. |
| `expected_long_mapping/` | **B, long-band mapping probe** (`capture/l3psy_mapping_probe.diff`, `capture/harness_mapping.c`) | Project extension, not encoder behaviour. The encoder's long-band mapping applied on every call to the long-block partition values before the block-switching branch. Equal to A bit for bit on long-path calls. |

Probe A line fields: `call`, `pe`, `block_type_reported` (the encoder's previous decision), `long_path`, `returned_ratio_l` (the previous call's ratio), `en`, `thm`.

Probe B line fields:
- `call`, `pe` and `encoder_long_path` (whether `pe < 1800`).
- `clip_samples`, `window_start`, `window_end` and `window_end_within_clip`.
- `window_nonzero_samples`: the nonzero int16 samples in the analysed window. 0 means exact digital silence.
- 21 values each of `en`, `thm`, `thm_quiet` (band mapping of threshold in quiet), `thm_from_quiet_partitions` (the part of `thm` from partitions where threshold in quiet decided) and `thm_signal` (band mapping of `min(nb, 2·nb₋₁, 16·nb₋₂)`).

Call `k` analyses stream samples `[576k − 768, 576k + 256)`, with zeros before sample 0. Probe B fills a final partial granule with zeros, as the dist10 encoder does.

## Contents

- `inputs/*.raw`: mono int16 little-endian, 44,100 Hz. Formulas and SHA-256 are in `inputs/inputs.json`. All inputs are whole granules except `target_2k_a1024_trunc6536`, which is the final-frame case.
- `provenance.json`: archive URL, commit and SHA-256; hashes of every source and table used; both probe patches, the probe B harness source and both binaries; container digest, compiler and flags; input and output hashes.
- `capture/`:
  - `make_inputs.py`: the signal generator.
  - `make_probe.py` and `make_probe_mapping.py`: generate the patches.
  - The two harnesses, and `run_in_container.sh`.
  - `verify_fixture.py`: checks hashes and the relations between the probes.
  - `summarize_controls.py`: prints the numbers quoted in the contract.
- `.gitattributes`: `-text`, so checkout keeps the hashed bytes exact.

## Verify

```bash
uv run python tests/fixtures/mpeg_psy_model2_dist10/capture/verify_fixture.py
```

This regenerates the inputs and checks every recorded hash. It also checks these relations exactly:
- Probe B equals probe A on long-path calls.
- The final-frame clip equals the full clip exactly on the calls whose window ends inside it.
- The silent masker's threshold is threshold in quiet alone.
- Floor energy coincides with a silent window.

It does not rerun dist10.

## Recapture

This needs Docker and the pinned archive. Download the archive into an empty directory of its own.

1. Extract `dist10/lsf/encoder/` into `build/src/`.
2. Generate both patched sources and compare each printed diff with the recorded one:

   ```bash
   python capture/make_probe.py build/src/l3psy.c build/src/l3psy_probe.c
   ```

   ```bash
   python capture/make_probe_mapping.py build/src/l3psy.c build/src/l3psy_mapping_probe.c
   ```

3. Copy `capture/harness.c` and `capture/harness_mapping.c` into `build/src/`, and `capture/run_in_container.sh` into `build/`.
4. Generate the inputs into `build/inputs/` with `make_inputs.py`.
5. Run the capture in the pinned container (replace `<build>` with the build directory):

   ```bash
   docker run --rm --network none -v <build>:/w gcc@sha256:d47e63a2a4621ff4da4e0825bfe8c52c2e65e957333d7878e5c3a6eee2cd525e sh /w/run_in_container.sh
   ```

6. From `build/out/encoder_path/*.jsonl` and `build/out/long_mapping/*.jsonl`, keep only the lines that start with `{`.

Reproducibility:
- The first capture (probe A) was byte-identical across two runs.
- The recapture from a fresh extraction reproduced probe A's outputs and binary byte for byte.
- Two runs of both probes were byte-identical.
