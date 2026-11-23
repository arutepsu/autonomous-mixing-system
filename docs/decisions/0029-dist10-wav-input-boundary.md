# ADR-029 — dist10 WAV input boundary as a client of the shared audio reader (2026-11-17)
- Status: Proposed (Task 3P; for Codex and owner review). ADR-028 is unchanged; this ADR adds one client edge to the exception it defines.
- Decision: the dist10 front end reads WAV files only through a new boundary module, `adapters.dsp_analysis.wav_input`, which is the second permitted `dsp_analysis` client of the shared `adapters.audio_io` component (after `file_analyzer`).
  1. Edge: `dsp_analysis.wav_input` may import the public names of `audio_io.integrity`, `audio_io.limits` and `audio_io.locations`, plus `core.records.artifacts`, the standard library and NumPy. It imports no other `dsp_analysis` module, rendering module, loop, stage, driver or evaluation code. No other `dsp_analysis` module may import the component; in particular the numerical front end (`front_end`) and its kernels stay free of file access.
  2. Responsibility: `read_dist10_pcm16(location, *, limits)` checks the declared sample rate and channel count against profile v1 (44,100 Hz, mono) before any file access. It then verifies the file with `check_resolved_wav_integrity`, whose `IntegrityError` and `LocationResolutionError` propagate unchanged. It accepts only the PCM_16 subtype, and converts the decoded float64 samples to the original signed 16-bit counts (× 32768, libsndfile's PCM_16 normalisation), requiring integral values within [−32768, 32767] before the cast. It returns the verified `AudioArtifact` (timeline metadata for later pair alignment) with a one-dimensional read-only `int16` array.
  3. Out of scope for this boundary: resampling, stereo fold-down, and the clipping or quantization of float or higher-bit-depth exports. These are rejected (`Dist10InputError` with a code), not converted, and belong to a separately specified profile.
- Source: MPEG front-end contract §2.1 (profile v1: 44.1 kHz, mono, int16 samples used without rescaling); ADR-028 (shared read-only audio access, exact client edges); ADR-027 rule (c).
- Rationale: the front end must read the same files under the same path, hash, limit and decoding policy as rendering and the level analyzer. Reading them inside the numerical front end would mix file access into pure kernels; a private reader would let the policies drift. One narrow boundary module keeps the kernels pure and the exception exact.
- Consequences:
  - `tests/architecture/test_adapters.py` lists the new client edge and the module's exact edges. `tests/architecture/test_shared_audio_access.py` adds allowed and forbidden import cases (private names, package form, rendering, `front_end` and `framing` importing the component) and a fresh-process isolation check (loads the shared reader and soundfile, no other `dsp_analysis` module, adapter or forbidden layer).
  - Producers must export profile-v1 stems as WAV, 44,100 Hz, mono, 16-bit signed integer PCM, with no sample-rate conversion; anything else is rejected with a named code.
  - Pair alignment, directional measurements and analyzer integration are not part of this decision. No requirement status changes.

## Implementation note (2026-11-20, P1 R1 handoff; Proposed, for Codex review)
- **Addition:** `read_dist10_member(artifact, *, root, max_file_bytes, max_decoded_bytes)` resolves one current member under its configured root (`resolve_artifact_location`) and calls `read_dist10_pcm16` unchanged.
- **Errors:** its expected resolution, integrity and profile failures are raised as one `Dist10ReadError` carrying the original code, with the original error chained. `read_dist10_pcm16` itself still propagates `IntegrityError` and `LocationResolutionError` unchanged.
- **Why here:** the new P1 modules (`masking_preparation`, `directional_analysis`) need verified, root-resolved reads. Routing them through this boundary keeps the edges exact:
  - no new `audio_io` client;
  - this module's edges are unchanged;
  - the new modules import only `wav_input`, never `audio_io`.
- **Pinned by:** `tests/architecture/test_adapters.py` (`ANALYSIS_EDGES`) and new regressions in `tests/unit/test_dsp_wav_input.py`.

## Implementation note (2026-11-22, exact dual mono; still Proposed)

The existing three reader/conversion functions gain keyword-only `allow_exact_dual_mono: bool = False`. The default retains native-mono v1. Explicit opt-in permits mono or exactly equal stereo PCM_16 at 44,100 Hz, after shared verification and full decoded shape/range/integrality checks of both channels. It selects channel 0 unchanged, retains the original verified stereo artifact/hash, and charges actual source channels. Unequal stereo is rejected. No new adapter, shared-reader client, conversion subsystem or copied WAV is introduced. Preparation and analysis derive opt-in only from the persisted v2 profile, with legacy pre-read admission preserved. Numerical kernels remain file-free and unchanged.
