# Extend the existing P1 input path for exact dual mono

> **For implementers:** Use superpowers:executing-plans and tests-first development. Modify the existing modules. Leave changes uncommitted for review. No new adapter, conversion pipeline or copied audio.

**Goal:** Let the existing P1 reader/preparer/analyzer accept verified 44.1 kHz PCM_16 files with one channel, or two exactly identical channels, while retaining mono DSP and truthful source metadata.

**Architecture:** Extend `wav_input.py`; pass the existing prepared profile through preparation and analysis; adjust the current metadata checks and evidence serialization. No new package, service, port, domain record, registry or generic channel-conversion abstraction.

**Tech stack:** Existing Python, NumPy and shared verified WAV reader. No dependency change.

**Spec:** Owner decision in this chat (2026-11-22): grow the existing implementation, prefer clean structure and avoid overbuilding. Existing front-end contract, P1 method and ADR-029 boundaries remain the basis. Exact duplicate-channel selection is an input representation, not a mixing correction or general stereo masking method.

## Starting state and design

HEAD is bb3e0a5. R2A, its documentation, the D1 audit and their handoffs are uncommitted. Preserve all that work. This document plans a new change; no implementation is authorized merely by its existence.

The owner superseded D1's separate-adapter/derived-WAV proposal. Keep reading original files, with original hashes and two-channel metadata. For exact dual mono, present one unchanged int16 count per frame to the existing numerical front end. Nothing is averaged, resampled, quantized or written as audio.

**One compatibility distinction is necessary:** existing prepared plans promise native mono. Keep `P1_PROFILE` (p1.dist10_mono_pcm16_44100@1) and direct-reader defaults strict. Add one opt-in value using the existing `profile` field: `P1_EXACT_DUAL_MONO_PROFILE = VersionRef("p1.dist10_mono_pcm16_44100", "2")`. Version 2 accepts mono or verified exact dual mono and always supplies one analysis channel. No new configuration record or framework.

This profile value is already encoded in prepared manifests, so it changes their hashes and bound analysis specifications. Existing v1 bytes/hashes remain valid. The geometric support digest continues to describe the canonical one-channel DSP cell grid under P1_PROFILE@1: matching cells can have the same geometric digest across ingress profiles, but the full prepared manifest/specification identity differs. Do not mistake the geometric digest for the complete input-policy fingerprint.

## Global constraints and review focus

- Keep DSP equations, window/history/eligibility behavior, numerical outputs, tables and pure clip-to-band input unchanged.
- Preserve original artifact channel count, verified hash, frame count, timeline and source requirements. Never relabel a stereo AudioArtifact as mono.
- Require equality over the entire verified clip, including outside the selected interval and in quiet/short/startup windows. No tolerance, gain matching, silence gate, offset search or fallback.
- Different stereo, inverted channels, higher channel counts, other rates/subtypes and forged malformed decoded arrays must fail explicitly. Do not read twice or bypass shared integrity/resource checks.
- Actual source channels determine read limits: a two-channel file costs two decoded channels even though analysis uses one.
- R2A remains INCOMPLETE, qualified metrics remain Unavailable, and no candidates/calibration or R5/CLI integration is introduced.
- No new adapter/classes, copied WAVs, device/provider calls, real HT01 analysis, installations or commits. Tests use temporary numerical fixtures only.

Review particularly: ignoring a last-frame/right-channel mismatch; hiding bad right-channel values behind equal/mono selection; allowing stereo under an old v1 plan; lying about source channel metadata; and silently changing persisted mono identities.

## Task 1 — Extend the existing verified reader

**Modify:** `src/autonomous_mixing/adapters/dsp_analysis/wav_input.py`; `tests/unit/test_dsp_wav_input.py`.

Retain names and existing arguments. Add keyword-only `allow_exact_dual_mono: bool = False` to `pcm16_counts`, `read_dist10_pcm16` and `read_dist10_member`. Reject non-bool option values. Existing callers/defaults stay native mono. This is the reader's narrow capability switch; preparation/analyzer derive it from their persisted profile, never a separate hidden global setting.

- [x] Write failing tests first, including strict-default rejection, opt-in mono acceptance and exact dual-mono acceptance.
- [x] Allow declared channel counts 1/2 only in opt-in mode, retaining rate/channel prechecks before I/O. Shared verification still checks actual versus declared metadata, full file hash, limits and finite decoding.
- [x] Require decoded array shape to equal `(artifact.frame_count, artifact.channel_count)` exactly, including empty clips and forged CheckedAudio fixtures; use an explicit shape/metadata failure for disagreement. Normalize ndarray subclasses before validation/computation, preserving existing safe behavior. Check range and integrality of both channels before selecting one. Use exact PCM_16 counts; reject a single LSB difference anywhere in the clip with a specific appended failure code such as NON_IDENTICAL_CHANNELS. Preserve existing failure codes and propagation.
- [x] Return the same fresh, plain, read-only one-dimensional int16 samples. One channel means identity; two equal channels means channel 0 (provably identical to channel 1). No mutation or writable shared result.
- [x] Keep Dist10Pcm16Clip fields intact. Small read-only properties `analysis_channel_count` (1) and `channel_mapping` (`mono_identity@1` / `exact_dual_mono_first_channel@1`) may expose the fixed applied rule; do not introduce a representation-record hierarchy. Source count remains `clip.artifact.channel_count`.
- [x] Test all 65,536 counts, extremes, empty/silent input, mismatch in first/last frame and beyond a selected interval, inverted channels, NaN/inf/out-of-range/non-integral right-channel values using labelled CheckedAudio fixtures, wrong metadata/shape/subtype, and source arrays unchanged. Keep existing mono outputs exact under NumPy error policies.
- [x] Prove the shared reader is called once with unmodified limits, original stereo metadata/hash is returned, and failure propagation remains unchanged. Exact dual mono must produce clip-to-band arrays identical to an equivalent temporary mono WAV.

## Task 2 — Use the existing profile through preparation and analysis

**Modify:** `core/records/masking_measurement.py`, `core/records/masking_diagnosis.py`, `adapters/dsp_analysis/masking_preparation.py`, `directional_analysis.py`, and their existing tests. Change only what the new input rule requires.

- [x] Write red tests for v2 stereo candidate metadata, successful physical verification, and rejection of genuinely different stereo before a successful prepared result. Cover mono/mono, mono/dual-mono and dual-mono/dual-mono pairs in both directions.
- [x] Add the one profile constant. The standalone MaskingPreparationJob can describe source candidates with 1 or 2 channels; that is not a proof of equality. MaskingPreparationRequest enforces profile-specific channel admissibility: v1 mono only; v2 one/two. Rate and ordered-source/geometry checks remain. Reject unknown profiles.
- [x] Preserve source requirements as real source metadata. R2A's MaskingDiagnosisPolicy also inspects every definition's sources against its full `measurement_plan.prepared.profile`, so its legacy v1 plan cannot accept stereo definitions just because the reusable job record now describes candidates. No change to the diagnosis source matcher or coverage semantics.
- [x] MaskingPreparer must pass the opted-in flag from request.profile, verify every physical file through read_dist10_member, and save `profile=request.profile` rather than today's hard-coded constant. Record only the original artifact identities/hashes. No DSP in preparation and no claim of qualified support.
- [x] MaskingPreparedPlan accepts the two profile values in its existing field. Keep its field layout and preparation JSON schema unchanged: the new profile is a value supported by the same format. Do not rewrite/upgrade old manifests. Pin a pre-change v1 canonical payload/hash as a golden regression; it must still decode and re-encode byte-identically.
- [x] Keep support_digest/PreparedSupport's canonical one-channel geometry unchanged. Test same-cell digest compatibility separately from differing full prepared digest/specification identity under v1/v2, and keep the existing bound_specification checks.
- [x] DirectionalMaskingAnalyzer must derive reader mode from the verified plan and add an explicit `_plan()` source-channel gate under `plan.prepared.profile` before any physical read; it has no such profile/channel gate today. Pin the early refusal with a no-read assertion, rather than relying only on the reader. Legacy v1 still rejects stereo; v2 still physically checks every current source, even when preparation succeeded earlier. A later changed unequal stereo artifact must not inherit the baseline's equality proof.
- [x] Preserve raw/qualified metric IDs, observation source IDs, declared source channel counts and analyzer numerical-method identity. Same int16 sequence must give identical numerical results and call metadata; this is not a new stereo psychoacoustic algorithm.
- [x] For extended-profile evidence, explicitly record original `source_channel_count`, `analysis_channel_count=1`, versioned `channel_mapping`, actual opted-in profile and canonical analysis profile. Emit raw evidence schema version 2 for this extended payload. Keep the old v1 evidence path/keys/version for legacy-profile requests so old mono evidence identity is unchanged; do not add a generic schema migration layer. New versioned evidence uses existing content-addressed storage.
- [x] Ensure representation fields are from the verified clip, not caller-supplied assertions. On unequal channels return failure, with no successful observations/manifest. Preserve all existing cancellation/time, byte bounds, read limits, routing and no-partial-success rules.

## Task 3 — Focused integration, compatibility and documentation

**Tests:** extend `test_masking_measurement_contract.py`, `test_masking_preparation.py`, `test_directional_analysis.py`, R2A contract/runtime tests and shared `masking_support.py` only where needed. Avoid a second fixture framework or broad unrelated refactor.

- [x] Red-to-green integration through the real preparer, analyzer/router and configured readiness source. Use temporary stereo files built from duplicated int16 arrays; compare actual band grids/scalars/call metadata against mono inputs for equivalent geometry. Compare data, not differing artifact/manifest identities.
- [x] Test mono and exact dual-mono silence, mixed source counts, reverse directions, two stereo channels charged against decode limits, old manifest/hash compatibility, old default reader behavior, and v1 rejection at request/analyzer/R2A policy boundaries.
- [x] Test that a single mismatch outside the requested analysis window is rejected during both preparation and later analysis. No existing successful preparation authorizes a changed file without re-verification.
- [x] Update exact architecture/public-name inventories for the single new profile constant and any changed imports/signatures. Add no new audio_io client, port or package. Existing data types and module responsibilities stay in place.
- [x] Run targeted faithful mutation probes: equality tolerance, skip right-channel validation, compare selected interval only, omit current recheck, pretend source has one channel, charge one decoded channel, ignore profile mode, and accept v2 under a v1 plan. Each relevant fault must fail a permanent test; restore source bytes exactly. Mark equivalent mutants honestly rather than padding probe counts.
- [x] Run locked focused tests with warnings as errors, fixture verifier, full suite, Ruff check/format, CLI help, lock and diff checks. Do not regenerate existing manifests or use HT01 as hidden expected test data.
- [x] Update front-end contract and P1 method in place to distinguish legacy native-mono ingress, exact-dual-mono ingress and always-mono analysis. Add a dated implementation note to ADR-029 only because it remains Proposed; do not edit accepted ADRs or create a new ADR merely for an extra adapter that is not being built. If a genuine new architectural decision emerges, flag it explicitly.
- [x] Append the research log: owner chose extending the existing boundary, superseding the separate-adapter/copied-WAV suggestion. Record exact interface changes, identity compatibility, tests and limits. Preserve all earlier log entries and uncommitted R2A/D1 work.
- [x] Leave uncommitted for review. No real HT01 call/analysis or audio output in this implementation. A later separately requested descriptive run can use the existing stereo Kick/Bass after review; Hat/Master remain genuinely stereo and are never silently converted.

## Completion

One existing input path accepts an explicitly selected, verified lossless duplicate-channel representation. DSP still consumes mono int16, original files/metadata remain intact, old native-mono plans still mean what they meant, and qualification remains unavailable. This is a small compatibility extension, not a new mixing tool or conversion subsystem.


## Builder check

A fresh read-only check found no architectural blocker or unnecessary subsystem. Its three clarifications are included above: decoded frame/channel shape must agree exactly, analyzer admission needs an explicit pre-read profile gate, and R2A policy must check every definition's source channels. Canonical support geometry and its digest stay unchanged; the existing outer profile/manifest fingerprint carries the input-policy distinction. No extra prepared-representation record is needed.

## Implementation outcome (2026-11-22; uncommitted for review)

All three tasks are implemented in existing components. The three reader functions have the explicit bool opt-in; v2 is persisted in the existing prepared profile and derived by preparation/analysis. Both complete channels are validated before exact comparison and selection. Existing original artifacts and decoded-channel resource accounting are retained. V1 request/analyzer/R2A gates remain strict; R2A is still INCOMPLETE, qualified values unavailable, and candidate generation unchanged.

Pre-change v1 prepared-manifest and raw-evidence bytes are pinned by two small checked-in golden JSON fixtures (SHA-256 `20a7e0b9950c9c3512bf5d89169ab664f7a0ee4e25c6b03d0f934d1d84a0d2ad` and `14b14d6bcf9b4fe36e4577c6c752383bb8dfc4475843c5ad106d49ef189eb323`). V2 uses the same manifest layout and schema; raw evidence alone is explicitly schema 2 with truthful representation metadata. Canonical support-cell digest remains unchanged; full prepared/spec identity changes.

Rulings: a focused `test_p1_dual_mono.py` reuses the existing World helper rather than spreading one integration feature across several files; no second fixture framework was added. Empty decoded arrays remain valid at the count conversion step, while zero-frame WAVs retain the shared verifier's existing refusal. Existing R2A/D1/conventions work was preserved in the owner's feature workspace. The earlier copied-WAV proposal is superseded by this extension.

Verification: 53 additional test cases relative to the R2A baseline; full suite 6,887 passed, 4 skipped. Twelve faithful targeted mutations were caught and every file restored byte-identically. One fresh read-only implementation review found no functional findings. No project audio was read or converted, no HT01 analysis was run, no provider/FL call or installation was made, and nothing was committed. This establishes representation compatibility, not calibrated masking or audible improvement.
