"""Tests for P1 support preparation and its manifest (R1 Task 2).

Inputs are the stored dist10 fixture signals (synthetic tones; unit-test signals, not evaluation data), written as
mono 44,100 Hz PCM_16 WAV files into ``tmp_path``. The fixture directory itself is only read.
"""

import dataclasses
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from masking_support import (
    ALIGNMENT,
    RATE,
    TA,
    TB,
    TC,
    Clock,
    Stems,
    fixture_counts,
    prep_job,
    prep_request,
)

import autonomous_mixing.adapters.dsp_analysis.masking_preparation as preparation
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import (
    MANIFEST_SCHEMA,
    MaskingPreparer,
    PreparationStoreError,
    PreparationStoreFailureCode,
    decode_prepared_plan,
    encode_prepared_plan,
    load_prepared_plan,
    manifest_path,
)
from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.masking_measurement import (
    AlignmentProvenance,
    BandRegion,
    MaskingPair,
    QualificationStatus,
    SupportKind,
)
from autonomous_mixing.core.records.observation import TimeRange
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success

N = 6912
FULL = TimeRange(0.0, N / RATE)
KNOWN = frozenset({ALIGNMENT.method})
Code = PreparationStoreFailureCode


@pytest.fixture
def stems(tmp_path) -> Stems:
    return Stems(tmp_path, {TA: fixture_counts("masker_1k_a8192"), TB: fixture_counts("target_2k_a1024")})


@pytest.fixture
def store(tmp_path) -> Path:
    path = tmp_path / "store"
    path.mkdir()
    return path


def preparer(stems, store, clock=None, known=KNOWN) -> MaskingPreparer:
    return MaskingPreparer(stems.roots, store=store, clock=clock or Clock(), known_alignments=known)


def both(stems, time_range=FULL, **kwargs):
    a, b = stems.artifacts[TA], stems.artifacts[TB]
    return prep_request(stems.state, stems.bindings, (prep_job(a, b, time_range, **kwargs),
                                                      prep_job(b, a, time_range, **kwargs)))  # fmt: skip


def prepared(stems, store, request=None):
    result = preparer(stems, store).prepare(request or both(stems))
    assert isinstance(result, Success), result
    # every successful preparation must reload under the loader's current size limit
    assert load_prepared_plan(store, result.value.digest) == result.value.plan
    return result.value


def tree(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


# --- geometry -------------------------------------------------------------------------------------------------


def test_full_clip_support_uses_calls_2_to_11_in_both_directions(stems, store):
    result = prepared(stems, store)
    ab, ba = result.plan.supports
    assert (ab.pair, ba.pair) == (MaskingPair(TA, TB), MaskingPair(TB, TA))
    for support in (ab, ba):
        assert (support.first_call, support.call_count, support.cell_count) == (2, 10, 200)
        assert (support.start_sample, support.stop_sample, support.frame_count) == (0, N, N)
    assert ab.masker_artifact_id == stems.artifacts[TA].artifact_id
    assert ba.masker_artifact_id == stems.artifacts[TB].artifact_id
    assert ab.digest != ba.digest
    assert result.plan.kind is SupportKind.GEOMETRIC and result.plan.qualification is QualificationStatus.UNCALIBRATED


def test_baseline_hashes_are_the_verified_file_hashes(stems, store):
    (ab, _) = prepared(stems, store).plan.supports
    assert ab.masker_sha256 == stems.artifacts[TA].content_hash
    assert ab.target_sha256 == stems.artifacts[TB].content_hash


def test_interval_keeps_only_complete_windows(stems, store):
    interval = TimeRange(385 / RATE, 2560 / RATE)  # call 2 starts at 384; call 4 ends at 2560
    (ab, _) = prepared(stems, store, both(stems, interval)).plan.supports
    assert (ab.start_sample, ab.stop_sample, ab.first_call, ab.call_count) == (385, 2560, 3, 2)


def test_short_interval_has_zero_calls_and_is_still_recorded(stems, store):
    (ab, _) = prepared(stems, store, both(stems, TimeRange(0.0, 1407 / RATE))).plan.supports
    assert ab.call_count == 0 and ab.cell_count == 0


def test_non_integral_interval_boundary_is_invalid_input(stems, store):
    result = preparer(stems, store).prepare(both(stems, TimeRange(0.0, 0.1 + 1e-9)))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_INPUT
    assert "integral sample position" in result.message and tree(store) == {}


def test_equal_count_different_regions_have_different_identity(stems, store):
    two = prepared(stems, store, both(stems, region=BandRegion("two", (1, 2)))).plan.supports[0]
    shifted = prepared(stems, store, both(stems, region=BandRegion("two", (7, 8)))).plan.supports[0]
    assert two.cell_count == shifted.cell_count and two.digest != shifted.digest


# --- inputs ---------------------------------------------------------------------------------------------------


def test_unknown_alignment_provenance_is_unavailable(stems, store):
    other = AlignmentProvenance(VersionRef("alignment.unknown", "1"), "undocumented")
    result = preparer(stems, store).prepare(both(stems, alignment=other))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNAVAILABLE
    assert "unknown alignment provenance" in result.message and tree(store) == {}


@pytest.mark.parametrize(("change", "code"), [({"rate": 48000}, "unsupported_sample_rate"),
                                              ({"subtype": "PCM_24"}, "unsupported_subtype"),
                                              ({"channels": 2}, "unsupported_channel_count")])  # fmt: skip
def test_files_outside_the_profile_never_reach_preparation_as_valid(tmp_path, store, change, code):
    stems = Stems(tmp_path, {TA: fixture_counts("masker_1k_a8192"), TB: fixture_counts("target_2k_a1024")}, **change)
    a, b = stems.artifacts[TA], stems.artifacts[TB]
    if "subtype" in change:
        result = preparer(stems, store).prepare(prep_request(stems.state, stems.bindings, (prep_job(a, b, FULL),)))
        assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNAVAILABLE and code in result.message
    else:  # the declared metadata is refused already by the request records
        with pytest.raises(ValueError, match="native mono 44100"):
            prep_job(a, b, FULL)


def test_altered_file_is_unavailable(stems, store):
    path = stems.root / stems.artifacts[TB].location
    data = bytearray(path.read_bytes())
    data[-2] ^= 1
    path.write_bytes(bytes(data))
    result = preparer(stems, store).prepare(both(stems))
    assert isinstance(result, PortFailure) and "hash_mismatch" in result.message and tree(store) == {}


def test_source_without_a_current_member_is_invalid_input(tmp_path, store):
    stems = Stems(tmp_path, {TA: fixture_counts("masker_1k_a8192"), TB: fixture_counts("target_2k_a1024"),
                             TC: fixture_counts("silence_12granules")})  # fmt: skip
    a, b = stems.artifacts[TA], stems.artifacts[TB]
    job = dataclasses.replace(prep_job(a, b, FULL), target_source=dataclasses.replace(
        prep_job(a, b, FULL).target_source, render_spec=VersionRef("render", "2")))  # fmt: skip
    result = preparer(stems, store).prepare(prep_request(stems.state, stems.bindings, (job,)))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_INPUT


def test_unconfigured_root_is_invalid_input(stems, store):
    result = MaskingPreparer({"other": "/x"}, store=store, clock=Clock(), known_alignments=KNOWN).prepare(both(stems))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_INPUT


def test_each_member_is_read_once(stems, store, monkeypatch):
    calls = []
    original = preparation.read_dist10_member

    def spy(artifact, **kwargs):
        calls.append(artifact.artifact_id)
        return original(artifact, **kwargs)

    monkeypatch.setattr(preparation, "read_dist10_member", spy)
    prepared(stems, store)
    assert calls == [stems.artifacts[TA].artifact_id, stems.artifacts[TB].artifact_id]


def test_original_files_are_not_modified(stems, store):
    before = tree(stems.root)
    prepared(stems, store)
    assert tree(stems.root) == before


# --- timing, cancellation and limits ----------------------------------------------------------------------------


def test_timing_is_recorded_from_clock_readings(stems, store):
    plan = prepared(stems, store).plan
    # readings: start (1), before each of the two reads (2, 3), finish (4); one second apart
    assert plan.timing.elapsed_seconds == 3.0 and plan.timing.finished_at > plan.timing.started_at
    assert (plan.max_file_bytes, plan.max_decoded_bytes) == (1 << 22, 1 << 22)


def test_cancellation_before_a_read_is_unavailable_and_writes_nothing(stems, store):
    result = preparer(stems, store, Clock(cancel_after=2)).prepare(both(stems))
    assert isinstance(result, PortFailure) and "cancelled" in result.message and tree(store) == {}


def test_elapsed_time_limit_is_enforced_before_a_read(stems, store):
    # readings 10 s apart: the first read starts at 10 s (allowed), the second at 20 s (beyond 15 s)
    request = prep_request(stems.state, stems.bindings, both(stems).jobs, max_elapsed_seconds=15.0)
    result = preparer(stems, store, Clock(step=10.0)).prepare(request)
    assert isinstance(result, PortFailure) and "time limit" in result.message and tree(store) == {}


def test_read_limits_from_the_request_apply(stems, store):
    request = prep_request(stems.state, stems.bindings, both(stems).jobs, max_file_bytes=1000)
    result = preparer(stems, store).prepare(request)
    assert isinstance(result, PortFailure) and "resource_limit" in result.message


# --- persistence ------------------------------------------------------------------------------------------------


def test_manifest_is_content_addressed_canonical_and_reloads_exactly(stems, store):
    result = prepared(stems, store)
    path = manifest_path(store, result.digest)
    payload = path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == result.digest and list(store.iterdir()) == [path]
    document = json.loads(payload)
    assert document["schema"] == MANIFEST_SCHEMA and document["kind"] == "geometric"
    assert document["qualification"] == "uncalibrated"
    assert document["supports"][0]["cells"][:3] == [[2, 1], [2, 2], [2, 3]]
    assert len(document["supports"][0]["cells"]) == 200
    assert load_prepared_plan(store, result.digest) == result.plan


def test_preparing_twice_is_idempotent_for_equal_plans(stems, store):
    first = prepared(stems, store)
    second = preparer(stems, store).prepare(both(stems))
    assert second.value.digest == first.digest and len(list(store.iterdir())) == 1


def test_missing_manifest_is_refused_not_regenerated(stems, store):
    result = prepared(stems, store)
    manifest_path(store, result.digest).unlink()
    with pytest.raises(PreparationStoreError) as raised:
        load_prepared_plan(store, result.digest)
    assert raised.value.code is Code.MISSING and list(store.iterdir()) == []


def test_tampered_manifest_is_refused(stems, store):
    result = prepared(stems, store)
    path = manifest_path(store, result.digest)
    path.write_bytes(path.read_bytes().replace(b'"call_count":10', b'"call_count":11'))
    with pytest.raises(PreparationStoreError) as raised:
        load_prepared_plan(store, result.digest)
    assert raised.value.code is Code.DIGEST_MISMATCH


def _rewrite(store: Path, document: dict) -> str:
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(payload).hexdigest()
    manifest_path(store, digest).write_bytes(payload)
    return digest


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda d: d.update(schema_version=2), Code.UNSUPPORTED_SCHEMA),
        (lambda d: d.update(schema="other"), Code.UNSUPPORTED_SCHEMA),
        (lambda d: d.update(qualification="qualified"), Code.MALFORMED),
        (lambda d: d.update(kind="qualified"), Code.MALFORMED),
        (lambda d: d["supports"][0].update(call_count=9), Code.MALFORMED),
        (lambda d: d["supports"][0]["cells"].pop(), Code.MALFORMED),
        (lambda d: d["supports"][0]["cells"].__setitem__(0, [2, 0]), Code.MALFORMED),
        (lambda d: d["supports"][0].update(digest="0" * 64), Code.MALFORMED),
        (lambda d: d["supports"][0].pop("alignment"), Code.MALFORMED),
        (lambda d: d["supports"][0].update(extra=1), Code.MALFORMED),
    ],
)
def test_self_consistent_but_invalid_manifests_are_refused(stems, store, mutate, code):
    document = json.loads(manifest_path(store, prepared(stems, store).digest).read_bytes())
    mutate(document)
    with pytest.raises(PreparationStoreError) as raised:
        load_prepared_plan(store, _rewrite(store, document))
    assert raised.value.code is code


def test_non_canonical_and_non_finite_manifests_are_refused(stems, store):
    payload = manifest_path(store, prepared(stems, store).digest).read_bytes()
    with pytest.raises(PreparationStoreError) as raised:
        decode_prepared_plan(json.dumps(json.loads(payload), indent=1, sort_keys=True).encode())
    assert raised.value.code is Code.NOT_CANONICAL
    with pytest.raises(PreparationStoreError) as raised:
        decode_prepared_plan(payload.replace(b'"elapsed_seconds":3.0', b'"elapsed_seconds":NaN'))
    assert raised.value.code is Code.MALFORMED


def test_failed_replace_leaves_no_manifest_and_no_temporary(stems, store, monkeypatch):
    def broken(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(preparation.os, "replace", broken)
    result = preparer(stems, store).prepare(both(stems))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNAVAILABLE
    assert "disk full" in result.message and list(store.iterdir()) == []


def test_missing_store_directory_is_unavailable(stems, tmp_path):
    result = preparer(stems, tmp_path / "absent").prepare(both(stems))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNAVAILABLE


def test_digest_argument_cannot_name_another_path(store):
    with pytest.raises(ValueError):
        load_prepared_plan(store, "../" + "0" * 61)


def test_encoding_is_deterministic(stems, store):
    plan = prepared(stems, store).plan
    assert encode_prepared_plan(plan) == encode_prepared_plan(dataclasses.replace(plan))


def test_constructor_validates_its_configuration(store):
    with pytest.raises(ValueError):
        MaskingPreparer([("inputs", "/x")], store=store, clock=Clock(), known_alignments=KNOWN)
    with pytest.raises(ValueError):
        MaskingPreparer({}, store=store, clock=object(), known_alignments=KNOWN)
    with pytest.raises(ValueError):
        MaskingPreparer({}, store=store, clock=Clock(), known_alignments={ALIGNMENT.method})


def test_fixture_inputs_are_unchanged():
    data = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "mpeg_psy_model2_dist10" / "inputs"
                       / "inputs.json").read_text())  # fmt: skip
    for name in ("masker_1k_a8192", "target_2k_a1024"):
        assert hashlib.sha256(fixture_counts(name).astype("<i2").tobytes()).hexdigest() == data[name]["sha256_int16le"]
    assert np.asarray(fixture_counts("masker_1k_a8192")).size == N


def test_cancellation_at_completion_writes_no_manifest(stems, store):
    # readings: start (1), before the two reads (2, 3), completion (4): cancelled only at completion
    result = preparer(stems, store, Clock(cancel_after=3)).prepare(both(stems))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNAVAILABLE
    assert "cancelled" in result.message and tree(store) == {}


def test_time_limit_exceeded_at_completion_writes_no_manifest(stems, store):
    # readings 10 s apart: reads at 10 s and 20 s after start are within 25 s; completion at 30 s is not
    request = prep_request(stems.state, stems.bindings, both(stems).jobs, max_elapsed_seconds=25.0)
    result = preparer(stems, store, Clock(step=10.0)).prepare(request)
    assert isinstance(result, PortFailure) and "time limit" in result.message and tree(store) == {}


# --- manifest size bound (Codex P2 review, 2026-11-22) -------------------------------------------------------------


def _manifest_size(stems, tmp_path) -> int:
    reference = tmp_path / "size-reference"
    reference.mkdir()
    return manifest_path(reference, prepared(stems, reference).digest).stat().st_size


def test_oversized_manifest_is_a_failure_and_writes_nothing(stems, store, tmp_path, monkeypatch):
    size = _manifest_size(stems, tmp_path)
    monkeypatch.setattr(preparation, "MAX_MANIFEST_BYTES", size // 2)
    result = preparer(stems, store).prepare(both(stems))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNAVAILABLE
    assert not result.retryable and "exceeds" in result.message and tree(store) == {}


def test_manifest_size_boundary_is_inclusive_and_reloads_under_the_same_limit(stems, store, tmp_path, monkeypatch):
    size = _manifest_size(stems, tmp_path)
    monkeypatch.setattr(preparation, "MAX_MANIFEST_BYTES", size)
    at_limit = prepared(stems, store)  # reloads under the same limit inside the helper
    assert manifest_path(store, at_limit.digest).stat().st_size == size
    below = tmp_path / "below"
    below.mkdir()
    monkeypatch.setattr(preparation, "MAX_MANIFEST_BYTES", size - 1)
    result = preparer(stems, below).prepare(both(stems))
    assert isinstance(result, PortFailure) and "exceeds" in result.message and tree(below) == {}
    with pytest.raises(PreparationStoreError) as raised:  # the loader agrees with the writer at the boundary
        load_prepared_plan(store, at_limit.digest)
    assert raised.value.code is Code.TOO_LARGE
