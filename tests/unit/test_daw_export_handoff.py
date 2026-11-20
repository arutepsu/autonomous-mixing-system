"""The file export handoff adapter: preparation, workspace identity, file rules and the shared verifier (plan rev. 3).

Real temporary directories and tiny synthetic WAVs written by the tests (unit-test data only, never evaluation data).
Symbolic links are created where the host allows it; such tests skip with the host's reason otherwise.
"""

import hashlib
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from daw_export_support import (
    CHECKPOINT,
    FORMAT,
    M1,
    ROOT_KEY,
    S1,
    S2,
    S3,
    TOKEN,
    export_request,
    manifest,
    snapshot,
    state,
    write_wav,
)

import autonomous_mixing.adapters.rendering.export_handoff as handoff_module
from autonomous_mixing.adapters.audio_io.limits import AudioReadLimits
from autonomous_mixing.adapters.audio_io.locations import resolve_artifact_location
from autonomous_mixing.adapters.rendering.export_handoff import FileExportHandoff, export_request_bytes
from autonomous_mixing.adapters.rendering.workspaces import WorkspaceRoot
from autonomous_mixing.core.admission.daw_export import assess_daw_import
from autonomous_mixing.core.ports.daw_export import validate_daw_export_preparation, validate_daw_files_verified
from autonomous_mixing.core.records.daw_export import (
    DAW_EXPORT_REQUEST_FILE,
    DawExportAwaiting,
    DawExportedFile,
    DawExportFormat,
    DawExportPreparationCode,
    DawExportPreparationFailure,
    DawFileIdentity,
    DawFilesVerified,
    DawImportRejected,
    DawImportRejectionCode,
    DawImportRequest,
    DawImportVerified,
)
from autonomous_mixing.core.records.identifiers import CheckpointId
from autonomous_mixing.core.records.project import ArtifactCheckpoint

Code = DawImportRejectionCode
LIMITS = AudioReadLimits(1 << 20, 1 << 20)


class Setup:
    def __init__(self, tmp_path, *, inputs=None, request=None, limits=LIMITS):
        self.tmp = tmp_path
        self.inputs = inputs or tmp_path / "inputs"
        self.inputs.mkdir(parents=True, exist_ok=True)
        for artifact in (S1, S2, S3, M1):
            (self.inputs / artifact.location).write_bytes(b"original " + artifact.artifact_id.value.encode())
        self.exports = tmp_path / "exports"
        self.exports.mkdir(exist_ok=True)
        self.request = request or export_request()
        self.handoff = self.make(limits=limits)

    def make(self, *, limits=LIMITS, state_record=None):
        protected = tuple(resolve_artifact_location(a, root=str(self.inputs)) for a in (S1, S2, S3, M1))
        return FileExportHandoff(root=WorkspaceRoot(ROOT_KEY, str(self.exports)), state=state_record or state(),
                                 protected_locations=protected, limits=limits)  # fmt: skip

    def originals(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(self.inputs.iterdir())}

    def prepare(self):
        result = self.handoff.prepare(self.request)
        validate_daw_export_preparation(self.request, result)
        assert isinstance(result, DawExportAwaiting), result
        self.awaiting = result
        self.directory = Path(result.workspace_directory)
        return result

    def export(self, **overrides):
        """Write the three outputs (distinct content) and return their hashes by artifact ID."""
        hashes = {}
        for index, output in enumerate(self.request.outputs):
            options = {"channels": output.channel_count, "subtype": self.request.format.subtype}
            options.update(overrides.get(output.file_name, {}))
            values = options.pop("values", None)
            frames = options.pop("frames", self.request.timeline.frame_count)
            channels = options["channels"]
            data = values if values is not None else np.full((frames, channels), 0.125 * (index + 1))
            hashes[output.artifact_id] = write_wav(self.directory / output.file_name, frames=frames, values=data,
                                                   rate=options.pop("rate", 44100), **options)  # fmt: skip
        self.hashes = hashes
        return hashes

    def manifest(self, files=None):
        listed = files or tuple(DawExportedFile(o.artifact_id, o.file_name, self.hashes[o.artifact_id])
                                for o in self.request.outputs)  # fmt: skip
        return manifest(self.awaiting, listed)

    def import_files(self, manifest_record=None, handoff=None, awaiting=None):
        request = DawImportRequest(awaiting or self.awaiting, manifest_record or self.manifest())
        result = (handoff or self.handoff).import_files(request)
        validate_daw_files_verified(request, result)
        return result


@pytest.fixture
def setup(tmp_path):
    return Setup(tmp_path)


def code_of(result):
    assert isinstance(result, DawImportRejected), result
    return result.code


def listing(directory):
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in directory.iterdir())


# --- preparation --------------------------------------------------------------------------------------------------------


def test_preparation_persists_the_request_exclusively_and_records_identities(setup):
    before = setup.originals()
    awaiting = setup.prepare()
    path = setup.directory / DAW_EXPORT_REQUEST_FILE
    payload = path.read_bytes()
    assert payload == export_request_bytes(setup.request)
    assert json.loads(payload)["export_request"]["export_id"] == "export_0001"
    assert awaiting.request_size == len(payload) and awaiting.request_fingerprint == hashlib.sha256(payload).hexdigest()
    status, directory_status = os.lstat(path), os.lstat(setup.directory)
    assert awaiting.request_file_identity == DawFileIdentity(status.st_dev, status.st_ino)
    assert awaiting.directory_identity == DawFileIdentity(directory_status.st_dev, directory_status.st_ino)
    assert setup.directory == setup.exports.resolve() / TOKEN and os.listdir(setup.directory) == [
        DAW_EXPORT_REQUEST_FILE
    ]
    assert setup.originals() == before  # originals untouched


def test_preparation_refuses_a_state_that_is_not_the_requests_before_checkpoint(setup):
    other = ArtifactCheckpoint(CheckpointId("c2"), CHECKPOINT.project_id, CHECKPOINT.revision, CHECKPOINT.artifacts)
    result = setup.handoff.prepare(export_request(before_checkpoint=other))
    assert isinstance(result, DawExportPreparationFailure)
    assert result.code is DawExportPreparationCode.REQUEST_STATE_MISMATCH
    assert list(setup.exports.iterdir()) == []


def test_a_second_preparation_never_reuses_or_overwrites_the_workspace(setup):
    setup.prepare()
    payload = (setup.directory / DAW_EXPORT_REQUEST_FILE).read_bytes()
    again = setup.handoff.prepare(setup.request)
    assert isinstance(again, DawExportPreparationFailure)
    assert again.code is DawExportPreparationCode.WORKSPACE_UNAVAILABLE
    assert (setup.directory / DAW_EXPORT_REQUEST_FILE).read_bytes() == payload


def test_a_workspace_overlapping_protected_files_is_refused(tmp_path):
    overlapping = Setup(tmp_path, inputs=tmp_path / "exports" / TOKEN)
    before = overlapping.originals()
    result = overlapping.handoff.prepare(overlapping.request)
    assert isinstance(result, DawExportPreparationFailure)
    assert result.code is DawExportPreparationCode.WORKSPACE_OVERLAPS_PROTECTED
    assert overlapping.originals() == before


def test_a_request_that_cannot_be_persisted_is_reported(setup, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(handoff_module.os, "open", refuse)
    result = setup.handoff.prepare(setup.request)
    assert isinstance(result, DawExportPreparationFailure)
    assert result.code is DawExportPreparationCode.REQUEST_NOT_PERSISTED


# --- a successful import --------------------------------------------------------------------------------------------------


def test_a_complete_stereo_export_is_verified_without_writes_and_assessed_in_core(setup):
    setup.prepare()
    setup.export()
    before = listing(setup.directory)
    files = setup.import_files()
    assert isinstance(files, DawFilesVerified)
    assert listing(setup.directory) == before  # nothing written, renamed or converted
    assert [e.verified_sha256 for e in files.evidence] == [setup.hashes[o.artifact_id] for o in setup.request.outputs]
    assert {(e.container, e.subtype) for e in files.evidence} == {("WAV", "PCM_24")}
    result = assess_daw_import(setup.awaiting, files, before_import=snapshot(), after_import=snapshot())
    assert isinstance(result, DawImportVerified)
    assert [a.channel_count for a in result.candidate_artifacts] == [2, 2, 2]  # stereo stays stereo
    for artifact in result.candidate_artifacts:
        located = resolve_artifact_location(artifact, root=str(setup.exports))
        assert Path(located.absolute_path).parent == setup.directory


# --- workspace and request-file identity ------------------------------------------------------------------------------------


def test_a_directory_replaced_at_the_same_path_with_a_copied_request_is_refused(setup):
    setup.prepare()
    setup.export()
    moved = setup.tmp / "moved"
    os.rename(setup.directory, moved)
    os.mkdir(setup.directory)
    for item in moved.iterdir():
        shutil.copyfile(item, setup.directory / item.name)
    assert (setup.directory / DAW_EXPORT_REQUEST_FILE).read_bytes() == (moved / DAW_EXPORT_REQUEST_FILE).read_bytes()
    assert code_of(setup.import_files()) is Code.WORKSPACE_REPLACED


def test_a_directory_replaced_with_the_original_request_file_moved_into_it_is_refused(setup):
    setup.prepare()
    setup.export()
    moved = setup.tmp / "moved"
    os.rename(setup.directory, moved)
    os.mkdir(setup.directory)
    for item in moved.iterdir():
        os.rename(item, setup.directory / item.name)  # a rename keeps every file's identity
    status = os.lstat(setup.directory / DAW_EXPORT_REQUEST_FILE)
    assert DawFileIdentity(status.st_dev, status.st_ino) == setup.awaiting.request_file_identity
    assert code_of(setup.import_files()) is Code.WORKSPACE_REPLACED


def test_a_request_file_replaced_by_an_identical_copy_is_refused(setup):
    setup.prepare()
    setup.export()
    path = setup.directory / DAW_EXPORT_REQUEST_FILE
    copy = setup.tmp / "copy.json"
    shutil.copyfile(path, copy)
    path.unlink()
    shutil.copyfile(copy, path)
    assert code_of(setup.import_files()) is Code.WORKSPACE_REPLACED


def test_a_request_file_replaced_between_its_lstat_and_its_open_is_refused(setup, monkeypatch):
    setup.prepare()
    setup.export()
    original = handoff_module.os.open

    def swap_then_open(path, flags, *args):
        if Path(path).name == DAW_EXPORT_REQUEST_FILE and not flags & os.O_WRONLY:
            data = Path(path).read_bytes()
            Path(path).unlink()
            Path(path).write_bytes(data)
        return original(path, flags, *args)

    monkeypatch.setattr(handoff_module.os, "open", swap_then_open)
    assert code_of(setup.import_files()) is Code.WORKSPACE_REPLACED


@pytest.mark.parametrize("change", ["grow", "edit"])
def test_a_request_file_changed_in_place_is_refused(setup, change):
    setup.prepare()
    setup.export()
    path = setup.directory / DAW_EXPORT_REQUEST_FILE
    with open(path, "r+b") as stream:
        if change == "grow":
            stream.seek(0, os.SEEK_END)
            stream.write(b" ")
        else:
            stream.write(b"[")
    assert code_of(setup.import_files()) is Code.REQUEST_MISMATCH


def test_the_request_file_read_is_bounded_by_the_recorded_size(setup, monkeypatch):
    setup.prepare()
    setup.export()
    with open(setup.directory / DAW_EXPORT_REQUEST_FILE, "ab") as stream:
        stream.write(b"x" * 10_000)
    reads = []
    original = handoff_module.os.read
    monkeypatch.setattr(handoff_module.os, "read", lambda fd, n: reads.append(n) or original(fd, n))
    assert code_of(setup.import_files()) is Code.REQUEST_MISMATCH
    assert sum(reads) <= setup.awaiting.request_size + 1


def test_an_awaiting_record_holding_another_request_is_refused(setup):
    setup.prepare()
    setup.export()
    altered = replace(setup.awaiting, request=export_request(export_id="export_0002"))
    listed = manifest(altered, setup.manifest().files)
    assert code_of(setup.import_files(listed, awaiting=altered)) is Code.REQUEST_MISMATCH


def test_a_changed_state_or_workspace_policy_is_refused(setup):
    setup.prepare()
    setup.export()
    other = ArtifactCheckpoint(CheckpointId("c2"), CHECKPOINT.project_id, CHECKPOINT.revision, CHECKPOINT.artifacts)
    assert code_of(setup.import_files(handoff=setup.make(state_record=state(other)))) is Code.REQUEST_MISMATCH
    (setup.inputs / S3.location).unlink()  # a protected original disappeared
    assert code_of(setup.import_files()) is Code.WORKSPACE_CHANGED


# --- manifest and listing ---------------------------------------------------------------------------------------------------


def test_manifest_mismatches(setup):
    setup.prepare()
    setup.export()
    files = setup.manifest().files
    assert code_of(setup.import_files(replace(setup.manifest(), request_fingerprint="b" * 64))) is Code.REQUEST_MISMATCH
    assert code_of(setup.import_files(replace(setup.manifest(), export_id="export_0002"))) is Code.REQUEST_MISMATCH
    assert code_of(setup.import_files(setup.manifest(files[:2]))) is Code.INCOMPLETE_COVERAGE
    renamed = (replace(files[0], file_name="other.wav"), *files[1:])
    assert code_of(setup.import_files(setup.manifest(renamed))) is Code.REQUEST_MISMATCH
    wrong_hash = (replace(files[0], content_hash="0" * 64), *files[1:])
    assert code_of(setup.import_files(setup.manifest(wrong_hash))) is Code.HASH_MISMATCH


def test_a_missing_output_is_incomplete_coverage(setup):
    setup.prepare()
    setup.export()
    (setup.directory / "pad.wav").unlink()
    assert code_of(setup.import_files()) is Code.INCOMPLETE_COVERAGE


@pytest.mark.parametrize("stray", ["notes.txt", "extra.wav", "folder"])
def test_any_stray_entry_is_refused(setup, stray):
    setup.prepare()
    setup.export()
    if stray == "folder":
        (setup.directory / stray).mkdir()
    else:
        (setup.directory / stray).write_bytes(b"x")
    assert code_of(setup.import_files()) is Code.UNEXPECTED_FILE


def test_a_listed_name_differing_only_in_case_is_a_collision(setup):
    setup.prepare()
    setup.export()
    os.rename(setup.directory / "bass.wav", setup.directory / "tmp.wav")
    os.rename(setup.directory / "tmp.wav", setup.directory / "BASS.wav")
    assert code_of(setup.import_files()) is Code.NAME_COLLISION


# --- file objects -----------------------------------------------------------------------------------------------------------


def test_a_symlinked_wav_is_refused(setup):
    setup.prepare()
    setup.export()
    outside = setup.tmp / "outside.wav"
    os.replace(setup.directory / "bass.wav", outside)
    try:
        os.symlink(outside, setup.directory / "bass.wav")
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"the host cannot create a symbolic link: {error}")
    assert code_of(setup.import_files()) is Code.LINKED_FILE


def test_a_hard_linked_wav_is_refused(setup):
    setup.prepare()
    setup.export()
    outside = setup.tmp / "outside.wav"
    os.link(setup.directory / "bass.wav", outside)
    assert os.lstat(setup.directory / "bass.wav").st_nlink == 2
    assert code_of(setup.import_files()) is Code.LINKED_FILE


def test_one_physical_file_under_two_names_is_refused(setup):
    setup.prepare()
    setup.export()
    (setup.directory / "pad.wav").unlink()
    os.link(setup.directory / "bass.wav", setup.directory / "pad.wav")
    files = setup.manifest(tuple(DawExportedFile(o.artifact_id, o.file_name, setup.hashes[setup.request.outputs[0].artifact_id]
                                                 if o.file_name == "pad.wav" else setup.hashes[o.artifact_id])
                                 for o in setup.request.outputs))  # fmt: skip
    assert code_of(setup.import_files(files)) is Code.DUPLICATE_PHYSICAL_FILE


def test_a_directory_in_place_of_a_wav_is_refused(setup):
    setup.prepare()
    setup.export()
    (setup.directory / "pad.wav").unlink()
    (setup.directory / "pad.wav").mkdir()
    assert code_of(setup.import_files()) is Code.LOCATION


def test_a_file_replaced_during_its_verification_is_refused(setup, monkeypatch):
    setup.prepare()
    setup.export()
    original = handoff_module.check_resolved_wav_integrity

    def verify_then_replace(location, **limits):
        checked = original(location, **limits)
        path = Path(location.absolute_path)
        data = path.read_bytes()
        path.unlink()
        path.write_bytes(data)
        return checked

    monkeypatch.setattr(handoff_module, "check_resolved_wav_integrity", verify_then_replace)
    assert code_of(setup.import_files()) is Code.WORKSPACE_CHANGED


def backdate(setup):
    """Move every output's timestamps 10 s into the past so an in-place write changes them on any clock."""
    for output in setup.request.outputs:
        path = setup.directory / output.file_name
        stamp = os.stat(path).st_mtime_ns - 10_000_000_000
        os.utime(path, ns=(stamp, stamp))


def change_one_byte(path):
    """Overwrite one data byte in place: same file object, same size, different content."""
    with open(path, "r+b") as stream:
        stream.seek(-1, os.SEEK_END)
        last = stream.read(1)
        stream.seek(-1, os.SEEK_END)
        stream.write(bytes([last[0] ^ 0x01]))


def test_a_file_changed_in_place_right_after_its_verification_is_refused(setup, monkeypatch):
    setup.prepare()
    setup.export()
    backdate(setup)
    original = handoff_module.check_resolved_wav_integrity

    verified = []

    def verify_then_edit(location, **limits):
        checked = original(location, **limits)
        verified.append(Path(location.absolute_path).name)
        if Path(location.absolute_path).name == "bass.wav":
            before = os.lstat(location.absolute_path)
            change_one_byte(Path(location.absolute_path))
            after = os.lstat(location.absolute_path)
            assert (after.st_ino, after.st_size, after.st_nlink) == (before.st_ino, before.st_size, before.st_nlink)
        return checked

    monkeypatch.setattr(handoff_module, "check_resolved_wav_integrity", verify_then_edit)
    assert code_of(setup.import_files()) is Code.WORKSPACE_CHANGED
    assert verified == ["bass.wav"]  # refused at once: no later file is read after the change is observed


def test_an_earlier_file_changed_during_a_later_files_verification_is_refused(setup, monkeypatch):
    setup.prepare()
    setup.export()
    backdate(setup)
    original = handoff_module.check_resolved_wav_integrity

    def edit_earlier_then_verify(location, **limits):
        if Path(location.absolute_path).name == "master.wav":  # the last output: bass.wav was already verified
            change_one_byte(setup.directory / "bass.wav")
        return original(location, **limits)

    monkeypatch.setattr(handoff_module, "check_resolved_wav_integrity", edit_earlier_then_verify)
    result = setup.import_files()
    assert code_of(result) is Code.WORKSPACE_CHANGED and "bass.wav" in result.explanation


def test_an_in_place_change_of_the_request_file_during_the_import_is_refused(setup, monkeypatch):
    setup.prepare()
    setup.export()
    request_file = setup.directory / DAW_EXPORT_REQUEST_FILE
    stamp = os.stat(request_file).st_mtime_ns - 10_000_000_000
    os.utime(request_file, ns=(stamp, stamp))
    original = handoff_module.check_resolved_wav_integrity

    def edit_request_then_verify(location, **limits):
        if Path(location.absolute_path).name == "master.wav":
            change_one_byte(request_file)
        return original(location, **limits)

    monkeypatch.setattr(handoff_module, "check_resolved_wav_integrity", edit_request_then_verify)
    assert code_of(setup.import_files()) is Code.WORKSPACE_CHANGED


# --- content, through the shared verifier ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override, code",
    [
        ({"subtype": "PCM_16"}, Code.ENCODING),
        ({"channels": 1}, Code.DIMENSIONS),
        ({"rate": 48000}, Code.DIMENSIONS),
        ({"frames": 7}, Code.DIMENSIONS),
    ],
)
def test_content_mismatches_are_refused_without_conversion(setup, override, code):
    setup.prepare()
    setup.export(**{"pad.wav": override})
    assert code_of(setup.import_files()) is code


def test_non_finite_float_samples_are_refused(tmp_path):
    floating = Setup(tmp_path, request=export_request(format=DawExportFormat("WAV", "FLOAT")))
    floating.prepare()
    values = np.zeros((8, 2))
    values[3, 1] = np.nan
    floating.export(**{"master.wav": {"values": values}})
    assert code_of(floating.import_files()) is Code.NONFINITE


def test_the_configured_read_limit_is_enforced(tmp_path):
    limited = Setup(tmp_path, limits=AudioReadLimits(16, 1 << 20))
    limited.prepare()
    limited.export()
    assert code_of(limited.import_files()) is Code.RESOURCE_LIMIT


def test_the_requested_format_is_exact(setup):
    assert FORMAT == DawExportFormat("WAV", "PCM_24")
    setup.prepare()
    setup.export(**{"bass.wav": {"subtype": "FLOAT"}})
    assert code_of(setup.import_files()) is Code.ENCODING


# --- Increment 3: the state inventory -------------------------------------------------------------------------


def test_an_inventory_state_that_is_not_the_requests_before_state_is_refused(setup):
    other = ArtifactCheckpoint(CheckpointId("c2"), CHECKPOINT.project_id, CHECKPOINT.revision, CHECKPOINT.artifacts)
    protected = tuple(resolve_artifact_location(a, root=str(setup.inputs)) for a in (S1, S2, S3, M1))
    lying = FileExportHandoff(root=WorkspaceRoot(ROOT_KEY, str(setup.exports)), limits=LIMITS,
                              inventory=lambda checkpoint: (state(other), protected))  # fmt: skip
    result = lying.prepare(setup.request)
    assert isinstance(result, DawExportPreparationFailure)
    assert result.code is DawExportPreparationCode.REQUEST_STATE_MISMATCH and list(setup.exports.iterdir()) == []


def test_the_handoff_takes_either_a_fixed_state_or_an_inventory(setup):
    with pytest.raises(ValueError):
        FileExportHandoff(root=WorkspaceRoot(ROOT_KEY, str(setup.exports)), limits=LIMITS)
    with pytest.raises(ValueError):
        FileExportHandoff(root=WorkspaceRoot(ROOT_KEY, str(setup.exports)), limits=LIMITS, state=state(),
                          protected_locations=(), inventory=lambda checkpoint: None)  # fmt: skip
