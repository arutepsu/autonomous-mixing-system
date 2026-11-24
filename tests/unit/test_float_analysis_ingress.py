"""Numerical regression fixtures only; no music or perceptual ground truth."""

import hashlib

import numpy as np
import pytest
from test_dsp_wav_input import LIMITS, located

from autonomous_mixing.adapters.dsp_analysis.wav_input import Dist10InputError, read_dist10_pcm16


@pytest.mark.parametrize("stereo", [False, True])
def test_explicit_float_profile_quantizes_reproducibly_without_alignment_change(tmp_path, stereo):
    values = np.array([-1.0, -1.5 / 32768, -0.5 / 32768, 0.0, 0.5 / 32768, 1.5 / 32768, 32767 / 32768, 1.0])
    samples = np.repeat(values[:, None], 2, axis=1) if stereo else values
    location = located(tmp_path, samples, subtype="DOUBLE", start=2.5)
    with pytest.raises(Dist10InputError):
        read_dist10_pcm16(location, limits=LIMITS, allow_exact_dual_mono=stereo)
    clips = [
        read_dist10_pcm16(location, limits=LIMITS, allow_exact_dual_mono=True, allow_float64_quantization=True)
        for _ in range(2)
    ]
    for clip in clips:
        assert clip.samples.tolist() == [-32768, -2, 0, 0, 0, 2, 32767, 32767]
        assert clip.artifact == location.artifact
        assert clip.artifact.timeline_start_seconds == 2.5
        assert clip.artifact.sample_rate_hz == 44100
        assert clip.subtype == "DOUBLE"
        assert clip.analysis_transform == "float64_to_pcm16_rint_even_saturate_no_dither@1"
        assert clip.analysis_sha256 == hashlib.sha256(clip.samples.astype("<i2").tobytes()).hexdigest()
    assert clips[0].analysis_sha256 == clips[1].analysis_sha256


@pytest.mark.parametrize("values", [[0.0, 0.0], [-1.01, 0.0], [0.0, 1.01]])
def test_float_silence_and_overload_policy(tmp_path, values):
    location = located(tmp_path, np.array(values), subtype="DOUBLE")
    if max(map(abs, values)) > 1:
        with pytest.raises(Dist10InputError, match="range"):
            read_dist10_pcm16(location, limits=LIMITS, allow_float64_quantization=True)
    else:
        assert not read_dist10_pcm16(location, limits=LIMITS, allow_float64_quantization=True).samples.any()


@pytest.mark.parametrize("subtype", ["FLOAT", "PCM_24", "PCM_32"])
def test_float_profile_keeps_other_encodings_unsupported(tmp_path, subtype):
    with pytest.raises(Dist10InputError):
        read_dist10_pcm16(
            located(tmp_path, np.zeros(8), subtype=subtype), limits=LIMITS, allow_float64_quantization=True
        )


@pytest.mark.parametrize("channels,rate", [(3, 44100), (1, 48000), (2, 44100)])
def test_float_profile_rejects_unsupported_geometry(tmp_path, channels, rate):
    samples = np.zeros((8, channels))
    if channels == 2:
        samples[0, 1] = 1 / 65536  # must compare before quantizing (both would round to zero)
    with pytest.raises(ValueError):
        read_dist10_pcm16(
            located(tmp_path, samples, rate=rate, subtype="DOUBLE"),
            limits=LIMITS,
            allow_exact_dual_mono=True,
            allow_float64_quantization=True,
        )


def test_rendered_peak_timestamp_is_canonical_and_samples_are_unchanged():
    import io

    import soundfile as sf
    from test_wav_output import _prepare

    from autonomous_mixing.adapters.rendering.wav_output import _canonical_peak_timestamp

    samples = np.array([[-0.5], [0.25], [0.0], [1.0]])
    snapshot, payload = _prepare(samples)
    position = payload.index(b"PEAK")
    assert payload[position + 12 : position + 16] == b"\x00" * 4
    changed = bytearray(payload)
    changed[position + 12 : position + 16] = (123456789).to_bytes(4, "little")
    assert _canonical_peak_timestamp(bytes(changed)) == payload
    decoded, _ = sf.read(io.BytesIO(payload), dtype="float64", always_2d=True)
    np.testing.assert_array_equal(decoded.view(np.uint64), snapshot.view(np.uint64))


def test_prepared_profile_reanalyzes_float_candidate_with_recorded_transform(tmp_path):
    import dataclasses
    import json

    from masking_support import TA, TB, World, fixture_counts

    from autonomous_mixing.adapters.dsp_analysis.directional_analysis import RAW_SIGNED_MARGIN_MEDIAN_DB
    from autonomous_mixing.core.records.masking_measurement import P1_FLOAT64_PROFILE
    from autonomous_mixing.core.records.results import Success

    world = World(
        tmp_path,
        {TA: fixture_counts("masker_1k_a8192"), TB: fixture_counts("target_2k_a1024")},
        profile=P1_FLOAT64_PROFILE,
    )
    before = world.analyzer.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)]))
    assert isinstance(before, Success), before
    original = world.stems.artifacts[TA]
    candidate = world.stems.write(
        TA, fixture_counts("masker_1k_a8192") // 2, rate=44100, subtype="DOUBLE", channels=1, start=0.0, revision="r2"
    )
    members = tuple(candidate if a == original else a for a in world.stems.state.audio.artifacts)
    # Artifact revision must agree with the checkpoint; this test isolates ingress, not execution lineage.
    candidate = dataclasses.replace(candidate, revision=original.revision)
    members = tuple(candidate if a == original else a for a in world.stems.state.audio.artifacts)
    state = dataclasses.replace(
        world.stems.state, audio=dataclasses.replace(world.stems.state.audio, artifacts=members)
    )
    from autonomous_mixing.core.records.integrity import ArtifactRootBinding

    after = world.analyzer.analyze(
        world.request(
            [world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)],
            state=state,
            bindings=tuple(ArtifactRootBinding(a.artifact_id, "inputs") for a in members),
        )
    )
    assert isinstance(after, Success), after
    docs = [json.loads(p.read_text()) for p in world.evidence_dir.glob("*.json")]
    sources = [d["sources"]["masker"] for d in docs]
    assert any(s["analysis_transform"] == "float64_to_pcm16_rint_even_saturate_no_dither@1" for s in sources)
    assert all(len(s["analysis_sha256"]) == 64 for s in sources)
