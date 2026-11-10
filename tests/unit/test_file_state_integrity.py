"""Tests for the file-backed state inspector (plan docs/superpowers/plans/2026-11-09-file-state-inspector-plan.md).

WAV files are tiny temporary numerical fixtures written to pytest's ``tmp_path``; they are not music or
evaluation data, and track identities are generic. Tests marked "native" exercise real files on this
platform; tests marked "injected" replace the resolver or verifier with a function raising a labelled
expected error, for conditions this platform cannot produce reliably (permission denial, link loops,
stale resolution, verifier-side path failures). A report records captured bytes and metadata during the
call only; it proves neither later immutability nor restoration.
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import autonomous_mixing.adapters.rendering.state_integrity as inspector_module
from autonomous_mixing.adapters.audio_io.integrity import CheckedAudio, IntegrityError, IntegrityFailureCode
from autonomous_mixing.adapters.audio_io.locations import LocationResolutionError, LocationResolutionFailureCode
from autonomous_mixing.adapters.rendering.state_integrity import FileStateIntegrityInspector
from autonomous_mixing.core.ports.integrity import StateIntegrityInspector, validate_state_integrity_response
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.integrity import (
    ArtifactRootBinding,
    IntegrityStatus,
    StateIntegrityReport,
    StateIntegrityRequest,
)
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)

UTC_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
RENDER = VersionRef("render", "1")
RATE = 8000
MONO = [[0.5], [-0.25], [0.125], [0.0]]
LIMIT = 1 << 20
VERIFIED, FAILED, UNCERTAIN = IntegrityStatus.VERIFIED, IntegrityStatus.FAILED, IntegrityStatus.UNCERTAIN
LCode, ICode = LocationResolutionFailureCode, IntegrityFailureCode


# --- fixtures ----------------------------------------------------------------------------------------


def _write(path: Path, samples, *, rate=RATE, subtype="FLOAT") -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.asarray(samples, dtype=np.float64), rate, format="WAV", subtype=subtype)
    return path.read_bytes()


def _artifact(aid, location, data, tracks, *, kind=ArtifactKind.STEM, revision="r2", rate=RATE, frames=4,
              channels=1, content_hash=None) -> AudioArtifact:  # fmt: skip
    return AudioArtifact(
        ArtifactId(aid), ProjectId("p1"), Revision(revision), tuple(TrackId(t) for t in tracks), kind, location,
        0.0, rate, channels, frames, content_hash or hashlib.sha256(data).hexdigest(), RENDER,
    )  # fmt: skip


def _state(members, gains) -> ProjectState:
    snapshot = ProjectSnapshot(ProjectId("p1"), Revision("r2"), UTC_NOW, (Track(TrackId("t1"), "track 1"),
                                                                          Track(TrackId("t2"), "track 2")))  # fmt: skip
    processing = ProjectProcessingState(
        ProjectId("p1"), Revision("r2"), tuple(TrackGainState(TrackId(t), a, 0.0) for t, a in gains)
    )
    return ProjectState(snapshot, processing, ArtifactCheckpoint(CheckpointId("cp-r2"), ProjectId("p1"), Revision("r2"), members))  # fmt: skip


class Scene:
    """Two roots: ``exports`` holds the current audio, ``inputs`` the superseded fixed input of t1."""

    def __init__(self, tmp: Path):
        self.exports, self.inputs = tmp / "exports", tmp / "inputs"
        self.roots = {"exports": str(self.exports), "inputs": str(self.inputs)}
        self.s1 = _artifact("s1", "stems/s1.wav", _write(self.exports / "stems" / "s1.wav", MONO), ("t1",))
        mix_path = self.exports / "mix.wav"
        self.mix = _artifact("mix", str(mix_path), _write(mix_path, MONO), ("t1", "t2"), kind=ArtifactKind.MIX)
        self.in_t2 = _artifact("in-t2", "in-t2.wav", _write(self.exports / "in-t2.wav", MONO), ("t2",), revision="r0")
        self.in_t1 = _artifact("in-t1", "in-t1.wav", _write(self.inputs / "in-t1.wav", MONO), ("t1",), revision="r0")
        self.state = _state((self.s1, self.mix, self.in_t2), (("t1", self.in_t1), ("t2", self.in_t2)))
        self.required = (self.s1, self.mix, self.in_t2, self.in_t1)

    def request(self, roots=None) -> StateIntegrityRequest:
        roots = roots or {"s1": "exports", "mix": "exports", "in-t2": "exports", "in-t1": "inputs"}
        return StateIntegrityRequest(
            self.state, tuple(ArtifactRootBinding(ArtifactId(a), key) for a, key in roots.items())
        )

    def file_hashes(self) -> dict[str, str]:
        files = sorted(p for root in (self.exports, self.inputs) for p in root.rglob("*") if p.is_file())
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


@pytest.fixture
def scene(tmp_path) -> Scene:
    return Scene(tmp_path)


def _inspector(roots, **limits) -> FileStateIntegrityInspector:
    return FileStateIntegrityInspector(roots, max_file_bytes=limits.get("files", LIMIT),
                                       max_decoded_bytes=limits.get("decoded", LIMIT))  # fmt: skip


def inspect(inspector, request) -> StateIntegrityReport:
    report = inspector.inspect(request)
    validate_state_integrity_response(request, report)
    return report


def _statuses(report):
    return [r.status for r in report.artifacts]


def _single(scene, artifact, root_key="exports"):
    """A request for a one-member state whose only gain input is the member itself."""
    state = _state((artifact,), (("t1", artifact),)) if artifact.kind is ArtifactKind.STEM else None
    return StateIntegrityRequest(state, (ArtifactRootBinding(artifact.artifact_id, root_key),))


# --- construction ------------------------------------------------------------------------------------


def test_the_inspector_satisfies_the_port(scene):
    inspector: StateIntegrityInspector = _inspector(scene.roots)
    assert callable(inspector.inspect)


@pytest.mark.parametrize(
    "roots",
    [[("exports", "/x")], {"": "/x"}, {" ": "/x"}, {1: "/x"}, {"exports": ""}, {"exports": None}, {"exports": 3}],
    ids=["not-mapping", "empty-key", "blank-key", "int-key", "empty-path", "none-path", "int-path"],
)
def test_constructor_validates_roots(roots):
    with pytest.raises(ValueError):
        _inspector(roots)


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1", None, np.int64(1)])
@pytest.mark.parametrize("name", ["files", "decoded"])
def test_constructor_requires_positive_builtin_int_limits(name, value):
    with pytest.raises(ValueError):
        _inspector({"exports": "/x"}, **{name: value})


def test_constructor_does_not_touch_the_filesystem(tmp_path):
    missing = tmp_path / "does-not-exist"
    _inspector({"exports": str(missing)})
    assert not missing.exists()


def test_roots_are_snapshotted_at_construction(scene):  # native
    roots = dict(scene.roots)
    inspector = _inspector(roots)
    roots["exports"] = str(scene.inputs)  # later caller mutation must not redirect inspection
    del roots["inputs"]
    assert _statuses(inspect(inspector, scene.request())) == [VERIFIED] * 4


# --- successful inspection (native) ------------------------------------------------------------------


def test_every_required_artifact_is_verified_with_its_observed_hash(scene):  # native
    before = scene.file_hashes()
    report = inspect(_inspector(scene.roots), scene.request())
    assert [r.artifact for r in report.artifacts] == list(scene.required)
    for result, artifact in zip(report.artifacts, scene.required, strict=True):
        assert result.artifact is artifact  # the original object, not a rebuilt one
        assert result.status is VERIFIED and result.observed_sha256 == artifact.content_hash
        assert result.explanation.strip()
    assert report.status is VERIFIED
    assert scene.file_hashes() == before  # read-only


def test_relative_and_absolute_locations_are_both_inspected(scene):  # native
    assert not Path(scene.s1.location).is_absolute() and Path(scene.mix.location).is_absolute()
    report = inspect(_inspector(scene.roots), scene.request())
    assert _statuses(report)[:2] == [VERIFIED, VERIFIED]


def test_superseded_fixed_input_is_inspected_under_its_own_root(scene):  # native
    report = inspect(_inspector(scene.roots), scene.request())
    assert report.artifacts[-1].artifact is scene.in_t1 and report.artifacts[-1].status is VERIFIED


def test_member_that_is_also_a_fixed_input_is_inspected_once(scene, monkeypatch):  # native, with a call spy
    calls = []
    original = inspector_module.check_resolved_wav_integrity

    def spy(location, **limits):
        calls.append(location.artifact.artifact_id)
        return original(location, **limits)

    monkeypatch.setattr(inspector_module, "check_resolved_wav_integrity", spy)
    inspect(_inspector(scene.roots), scene.request())
    assert calls == [a.artifact_id for a in scene.required]


def test_inspection_uses_the_configured_limits(scene, monkeypatch):  # native, with a call spy
    seen = []
    original = inspector_module.check_resolved_wav_integrity

    def spy(location, *, max_file_bytes, max_decoded_bytes):
        seen.append((max_file_bytes, max_decoded_bytes))
        return original(location, max_file_bytes=max_file_bytes, max_decoded_bytes=max_decoded_bytes)

    monkeypatch.setattr(inspector_module, "check_resolved_wav_integrity", spy)
    inspect(_inspector(scene.roots, files=10_000, decoded=20_000), scene.request())
    assert set(seen) == {(10_000, 20_000)}


# --- expected failures on real files (native) --------------------------------------------------------


def test_missing_file_fails_and_the_remaining_artifacts_are_still_inspected(scene):  # native
    (scene.exports / "stems" / "s1.wav").unlink()
    report = inspect(_inspector(scene.roots), scene.request())
    assert _statuses(report) == [FAILED, VERIFIED, VERIFIED, VERIFIED]
    first = report.artifacts[0]
    assert first.observed_sha256 is None and "not_found" in first.explanation
    assert report.status is FAILED


def test_changed_file_content_is_a_hash_mismatch_without_an_invented_hash(scene):  # native
    _write(scene.exports / "mix.wav", [[1.0], [1.0], [1.0], [1.0]])
    before = scene.file_hashes()
    report = inspect(_inspector(scene.roots), scene.request())
    mix = report.artifacts[1]
    assert (mix.status, mix.observed_sha256) == (FAILED, None)
    assert "hash_mismatch" in mix.explanation
    assert scene.file_hashes() == before


def test_metadata_mismatch_with_a_correct_hash_fails(scene):  # native
    path = scene.exports / "stems" / "wrong-rate.wav"
    wrong = _artifact("s1", "stems/wrong-rate.wav", _write(path, MONO), ("t1",), rate=RATE * 2)  # hash is correct
    report = inspect(_inspector(scene.roots), _single(scene, wrong))
    (result,) = report.artifacts
    assert (result.status, result.observed_sha256) == (FAILED, None) and "metadata_mismatch" in result.explanation


def test_invalid_wav_with_a_correct_hash_fails(scene):  # native
    path = scene.exports / "stems" / "not-audio.wav"
    path.write_bytes(b"not a wav file")
    artifact = _artifact("s1", "stems/not-audio.wav", path.read_bytes(), ("t1",))
    (result,) = inspect(_inspector(scene.roots), _single(scene, artifact)).artifacts
    assert result.status is FAILED and "invalid_wav" in result.explanation


def test_nonfinite_samples_fail(scene):  # native
    path = scene.exports / "stems" / "nan.wav"
    artifact = _artifact(
        "s1", "stems/nan.wav", _write(path, [[0.5], [np.nan], [0.25]], subtype="DOUBLE"), ("t1",), frames=3
    )
    (result,) = inspect(_inspector(scene.roots), _single(scene, artifact)).artifacts
    assert result.status is FAILED and "nonfinite_samples" in result.explanation


def test_a_directory_at_the_location_is_not_a_regular_file(scene):  # native
    (scene.exports / "folder.wav").mkdir()
    artifact = _artifact("s1", "folder.wav", b"", ("t1",))
    (result,) = inspect(_inspector(scene.roots), _single(scene, artifact)).artifacts
    assert result.status is FAILED and "not_regular_file" in result.explanation


def test_relative_location_bound_to_the_wrong_root_is_not_found(scene):  # native
    bindings = {"s1": "inputs", "mix": "exports", "in-t2": "exports", "in-t1": "inputs"}
    s1 = inspect(_inspector(scene.roots), scene.request(bindings)).artifacts[0]
    assert s1.status is FAILED and "not_found" in s1.explanation


def test_absolute_location_outside_its_bound_root_fails(scene):  # native
    bindings = {"s1": "exports", "mix": "inputs", "in-t2": "exports", "in-t1": "inputs"}  # mix lives in exports
    mix = inspect(_inspector(scene.roots), scene.request(bindings)).artifacts[1]
    assert mix.status is FAILED and "outside_root" in mix.explanation


def test_unsupported_location_fails(scene):  # native
    artifact = _artifact("s1", "file://host/s1.wav", b"x", ("t1",))
    (result,) = inspect(_inspector(scene.roots), _single(scene, artifact)).artifacts
    assert result.status is FAILED and "unsupported_path" in result.explanation


def test_root_that_is_not_a_directory_is_uncertain(scene):  # native
    roots = {**scene.roots, "exports": str(scene.exports / "mix.wav")}
    report = inspect(_inspector(roots), scene.request())
    assert _statuses(report) == [UNCERTAIN, UNCERTAIN, UNCERTAIN, VERIFIED]
    assert all("invalid_root" in r.explanation and r.observed_sha256 is None for r in report.artifacts[:3])
    assert report.status is UNCERTAIN


def test_file_size_limit_is_uncertain(scene):  # native
    report = inspect(_inspector(scene.roots, files=8), scene.request())
    assert _statuses(report) == [UNCERTAIN] * 4
    assert all("resource_limit" in r.explanation for r in report.artifacts)


def test_unconfigured_root_key_is_uncertain_and_inspection_continues(scene):  # native
    roots = {"exports": scene.roots["exports"]}  # "inputs" is not configured
    report = inspect(_inspector(roots), scene.request())
    assert _statuses(report) == [VERIFIED, VERIFIED, VERIFIED, UNCERTAIN]
    last = report.artifacts[-1]
    assert last.observed_sha256 is None and "inputs" in last.explanation and "not configured" in last.explanation


def test_failure_dominates_uncertainty_in_the_aggregate(scene):  # native
    (scene.exports / "stems" / "s1.wav").unlink()
    report = inspect(_inspector({"exports": scene.roots["exports"]}), scene.request())
    assert _statuses(report) == [FAILED, VERIFIED, VERIFIED, UNCERTAIN] and report.status is FAILED


# --- exhaustive error mapping (injected) -------------------------------------------------------------

LOCATION_STATUS = {
    LCode.NOT_FOUND: FAILED,
    LCode.UNSUPPORTED_PATH: FAILED,
    LCode.OUTSIDE_ROOT: FAILED,
    LCode.RESOLUTION_LOOP: FAILED,
    LCode.INVALID_ROOT: UNCERTAIN,
    LCode.NOT_READABLE: UNCERTAIN,
    LCode.STALE_RESOLUTION: UNCERTAIN,
}
INTEGRITY_STATUS = {
    ICode.NOT_FOUND: FAILED,
    ICode.UNSUPPORTED_PATH: FAILED,
    ICode.NOT_REGULAR_FILE: FAILED,
    ICode.HASH_MISMATCH: FAILED,
    ICode.INVALID_WAV: FAILED,
    ICode.METADATA_MISMATCH: FAILED,
    ICode.NONFINITE_SAMPLES: FAILED,
    ICode.NOT_READABLE: UNCERTAIN,
    ICode.RESOURCE_LIMIT: UNCERTAIN,
}


def test_the_expected_mapping_tables_cover_every_code():
    assert set(LOCATION_STATUS) == set(LCode) and set(INTEGRITY_STATUS) == set(ICode)


def _raise_for(target_id, error):
    def fail_one(artifact_or_location, **kwargs):
        artifact = getattr(artifact_or_location, "artifact", artifact_or_location)
        if artifact.artifact_id == target_id:
            raise error
        return ORIGINALS[fail_one.stage](artifact_or_location, **kwargs)

    return fail_one


ORIGINALS = {
    "resolve": inspector_module.resolve_artifact_location,
    "check": inspector_module.check_resolved_wav_integrity,
}


@pytest.mark.parametrize("code", list(LCode), ids=lambda c: c.value)
def test_location_errors_map_exactly_and_inspection_continues(scene, monkeypatch, code):  # injected
    fail = _raise_for(ArtifactId("mix"), LocationResolutionError(code, "injected resolver failure"))
    fail.stage = "resolve"
    monkeypatch.setattr(inspector_module, "resolve_artifact_location", fail)
    report = inspect(_inspector(scene.roots), scene.request())
    mix = report.artifacts[1]
    assert mix.status is LOCATION_STATUS[code] and mix.observed_sha256 is None
    assert code.value in mix.explanation and "injected resolver failure" in mix.explanation
    assert [r.status for i, r in enumerate(report.artifacts) if i != 1] == [VERIFIED] * 3


@pytest.mark.parametrize("code", list(ICode), ids=lambda c: c.value)
def test_integrity_errors_map_exactly_and_inspection_continues(scene, monkeypatch, code):  # injected
    fail = _raise_for(ArtifactId("in-t2"), IntegrityError(code, "injected verifier failure"))
    fail.stage = "check"
    monkeypatch.setattr(inspector_module, "check_resolved_wav_integrity", fail)
    report = inspect(_inspector(scene.roots), scene.request())
    item = report.artifacts[2]
    assert item.status is INTEGRITY_STATUS[code] and item.observed_sha256 is None
    assert code.value in item.explanation and "injected verifier failure" in item.explanation
    assert [r.status for i, r in enumerate(report.artifacts) if i != 2] == [VERIFIED] * 3


def test_stale_resolution_raised_by_the_verifier_is_uncertain(scene, monkeypatch):  # injected
    fail = _raise_for(ArtifactId("s1"), LocationResolutionError(LCode.STALE_RESOLUTION, "moved between calls"))
    fail.stage = "check"
    monkeypatch.setattr(inspector_module, "check_resolved_wav_integrity", fail)
    first = inspect(_inspector(scene.roots), scene.request()).artifacts[0]
    assert first.status is UNCERTAIN and "stale_resolution" in first.explanation


@pytest.mark.parametrize(
    "stage, error",
    [
        ("resolve", OSError("unrelated I/O failure")),
        ("check", OSError("unrelated I/O failure")),
        ("check", MemoryError()),
        ("check", RuntimeError("programming error")),
        ("resolve", ValueError("plain argument error")),
        ("check", ValueError("plain argument error")),
    ],
)
def test_unexpected_errors_propagate(scene, monkeypatch, stage, error):  # injected
    fail = _raise_for(ArtifactId("mix"), error)
    fail.stage = stage
    name = "resolve_artifact_location" if stage == "resolve" else "check_resolved_wav_integrity"
    monkeypatch.setattr(inspector_module, name, fail)
    with pytest.raises(type(error)):
        _inspector(scene.roots).inspect(scene.request())


def test_inspect_checks_the_request_type(scene):
    with pytest.raises(ValueError):
        _inspector(scene.roots).inspect(scene.state)


def test_the_observed_hash_comes_from_the_verifier_not_the_record(scene, monkeypatch):  # injected
    # A verifier reporting a hash other than the recorded one is inconsistent; the inspector must pass the
    # verifier's hash through, so the VERIFIED record rejects it instead of silently copying the record's.
    original = inspector_module.check_resolved_wav_integrity

    def inconsistent(location, **limits):
        checked = original(location, **limits)
        return CheckedAudio(checked.artifact, checked.samples, checked.container, checked.subtype, "f" * 64)

    monkeypatch.setattr(inspector_module, "check_resolved_wav_integrity", inconsistent)
    with pytest.raises(ValueError, match="VERIFIED"):
        _inspector(scene.roots).inspect(scene.request())
