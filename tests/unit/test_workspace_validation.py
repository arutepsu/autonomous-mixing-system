"""Tests for read-only revalidation of an ``AllocatedWorkspace`` record.

Fixtures are directories and tiny placeholder files created in ``tmp_path``; no project media or audio
content is used. Workspaces are created by the real allocator; afterwards the native filesystem is
changed with real operations (deletion, replacement, symbolic links and, on Windows, junctions) where
the host supports them, and such tests skip with the host's reason otherwise. Errors the host cannot
produce reliably (permission denials, link cycles inside ``Path.resolve``, ``os.lstat`` or ``os.stat``,
reparse attributes) are simulated with controlled doubles; those exercise branch mapping only and are
not evidence of real platform behaviour.
"""

import builtins
import dataclasses
import errno
import io
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import autonomous_mixing.adapters.rendering.revalidation as validation  # moved in ADR-027 step M3; tests patch or inspect its internals
import autonomous_mixing.adapters.rendering.workspaces as allocation  # moved in ADR-027 step M3; tests patch or inspect its internals
import autonomous_mixing.infrastructure.workspaces as workspaces
from autonomous_mixing.application.execution import ExecutionWorkspace
from autonomous_mixing.domain.project import (
    ArtifactCheckpoint,
    ArtifactKind,
    AudioArtifact,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.domain.shared import (
    ArtifactId,
    CheckpointId,
    ExecutionId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.infrastructure.filesystem import LocationResolutionError, resolve_artifact_location
from autonomous_mixing.infrastructure.workspaces import (
    AllocatedWorkspace,
    WorkspaceAllocationError,
    WorkspaceAllocationFailureCode,
    WorkspaceAllocationRequest,
    WorkspaceRoot,
    allocate_workspace,
    validate_allocated_workspace,
)

Code = WorkspaceAllocationFailureCode
TOKEN = "exec-1"
ROOT_KEY = "outputs"
WINDOWS = os.name == "nt"
_REAL = SimpleNamespace(
    resolve=Path.resolve, stat=os.stat, lstat=os.lstat, open=builtins.open, symlink=os.symlink, rmdir=os.rmdir
)


# --- metadata fixtures ------------------------------------------------------------------------


def _artifact(artifact_id, location, *, project="p1", tracks=("t1",), kind=ArtifactKind.STEM, revision="r1"):
    return AudioArtifact(
        ArtifactId(artifact_id),
        ProjectId(project),
        Revision(revision),
        tuple(TrackId(t) for t in tracks),
        kind,
        location,
        0.0,
        48000,
        1,
        4,
        "0" * 64,
        VersionRef("render", "1"),
    )


STEM_T1 = _artifact("stem-t1", "stem-t1.wav")
MIX = _artifact("mix", "mix.wav", tracks=("t1", "t2"), kind=ArtifactKind.MIX, revision="r2")
BASE_T2 = _artifact("base-t2", "base-t2.wav", tracks=("t2",), revision="r0")


def _state() -> ProjectState:
    project, revision = ProjectId("p1"), Revision("r2")
    snapshot = ProjectSnapshot(
        project,
        revision,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        (Track(TrackId("t1"), "one"), Track(TrackId("t2"), "two")),
    )
    processing = ProjectProcessingState(
        project,
        revision,
        (TrackGainState(TrackId("t1"), STEM_T1, 0.0), TrackGainState(TrackId("t2"), BASE_T2, -3.0)),
    )
    audio = ArtifactCheckpoint(CheckpointId("c1"), project, revision, (STEM_T1, MIX))
    return ProjectState(snapshot, processing, audio)


@pytest.fixture
def tree(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    for name in ("stem-t1.wav", "mix.wav", "base-t2.wav"):
        (sources / name).write_bytes(b"fixture")
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "earlier.txt").write_bytes(b"keep")
    (tmp_path / "other").mkdir()
    return tmp_path


def _inventory(tree, *extra) -> tuple:
    return tuple(resolve_artifact_location(a, root=str(tree / "sources")) for a in (STEM_T1, MIX, BASE_T2, *extra))


def _request(tree, *, token=TOKEN, protected=None, root_path=None, root_key=ROOT_KEY):
    return WorkspaceAllocationRequest(
        ExecutionWorkspace(ExecutionId("e1"), ROOT_KEY, token),
        WorkspaceRoot(root_key, str(root_path if root_path is not None else tree / "outputs")),
        _state(),
        protected if protected is not None else _inventory(tree),
    )


def _canonical(path) -> str:
    return str(_REAL.resolve(Path(path), strict=True))


@pytest.fixture
def allocated(tree) -> AllocatedWorkspace:
    return allocate_workspace(_request(tree))


def _fail(workspace, code) -> WorkspaceAllocationError:
    with pytest.raises(WorkspaceAllocationError) as info:
        validate_allocated_workspace(workspace)
    assert info.value.code is code, info.value
    assert str(info.value).startswith(f"{code.value}: ")
    assert info.value.created_directory is None
    return info.value


def _id(value):
    if isinstance(value, BaseException):
        return f"{type(value).__name__}-{getattr(value, 'errno', '')}"
    return repr(value)


class _Forbidden(AssertionError):
    pass


def _deny_all(monkeypatch, entries) -> list:
    calls = []

    def deny(name):
        def call(*args, **kwargs):
            calls.append(name)
            raise _Forbidden(name)

        return call

    for owner, name in entries:
        monkeypatch.setattr(owner, name, deny(f"{getattr(owner, '__name__', owner)}.{name}"))
    return calls


FILESYSTEM = [
    (Path, "resolve"),
    (Path, "mkdir"),
    (Path, "exists"),
    (Path, "stat"),
    (Path, "lstat"),
    (os, "stat"),
    (os, "lstat"),
    (os, "mkdir"),
    (os, "scandir"),
    (os, "listdir"),
    (os, "open"),
    (builtins, "open"),
    (io, "open"),
]
MUTATING_OR_CONTENT = [
    (os, "remove"),
    (os, "unlink"),
    (os, "rmdir"),
    (os, "rename"),
    (os, "replace"),
    (os, "mkdir"),
    (os, "makedirs"),
    (os, "open"),
    (os, "scandir"),
    (os, "listdir"),
    (shutil, "rmtree"),
    (shutil, "move"),
    (Path, "mkdir"),
    (Path, "unlink"),
    (Path, "rmdir"),
    (Path, "rename"),
    (Path, "replace"),
    (Path, "touch"),
    (Path, "open"),
    (Path, "iterdir"),
    (builtins, "open"),
    (io, "open"),
]


def _snapshot(base: Path) -> dict:
    entries = {}
    pending = [base]
    while pending:
        with os.scandir(pending.pop()) as listing:
            names = sorted(entry.path for entry in listing)
        for path in names:
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
                entries[path] = ("link", os.readlink(path))
            elif stat.S_ISDIR(st.st_mode):
                entries[path] = ("dir",)
                pending.append(Path(path))
            else:
                with _REAL.open(path, "rb") as handle:
                    entries[path] = ("file", handle.read(), st.st_mtime_ns)
    return entries


def _symlink(target, link, *, directory):
    try:
        _REAL.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"host cannot create symbolic links: {exc}")


def _junction(target, link):
    import _winapi

    try:
        _winapi.CreateJunction(str(target), str(link))
    except OSError as exc:
        pytest.skip(f"host cannot create a directory junction: {exc}")


# --- public surface and arguments -------------------------------------------------------------


def test_validator_is_exposed_by_the_workspace_facade_and_owned_by_the_validation_module():
    assert workspaces.validate_allocated_workspace is validation.validate_allocated_workspace
    assert validation.validate_allocated_workspace.__module__ == validation.__name__
    assert "validate_allocated_workspace" in workspaces.__all__ and len(workspaces.__all__) == 7


@pytest.mark.parametrize("value", [None, "workspace", object(), 1], ids=repr)
def test_non_record_arguments_are_plain_value_errors_before_filesystem_access(monkeypatch, value):
    touched = _deny_all(monkeypatch, FILESYSTEM)
    with pytest.raises(ValueError) as info:
        validate_allocated_workspace(value)  # type: ignore[arg-type]
    assert type(info.value) is ValueError and touched == []


def test_a_request_is_not_accepted_in_place_of_an_allocated_record(tree, monkeypatch):
    request = _request(tree)
    touched = _deny_all(monkeypatch, FILESYSTEM)
    with pytest.raises(ValueError) as info:
        validate_allocated_workspace(request)  # type: ignore[arg-type]
    assert type(info.value) is ValueError and touched == []


# --- success ----------------------------------------------------------------------------------


def test_fresh_allocation_validates_without_creating_opening_or_mutating_anything(tree, allocated, monkeypatch):
    before = _snapshot(tree)
    fields = (allocated.request, allocated.resolved_root, allocated.directory)
    forbidden = _deny_all(monkeypatch, MUTATING_OR_CONTENT)

    def no_allocation(request):
        raise _Forbidden("allocate_workspace")

    monkeypatch.setattr(allocation, "allocate_workspace", no_allocation)
    monkeypatch.setattr(workspaces, "allocate_workspace", no_allocation)
    assert validate_allocated_workspace(allocated) is None
    monkeypatch.undo()
    assert forbidden == []
    assert (allocated.request, allocated.resolved_root, allocated.directory) == fields
    assert allocated.request is fields[0]
    assert _snapshot(tree) == before


def test_manually_constructed_equivalent_record_passes_inspection(tree, allocated):
    manual = AllocatedWorkspace(_request(tree), _canonical(tree / "outputs"), allocated.directory)
    assert manual is not allocated and manual.request is not allocated.request
    assert validate_allocated_workspace(manual) is None  # inspection, not proof of allocation provenance


def test_existing_outputs_do_not_prevent_repeated_validation(tree, allocated):
    first = Path(allocated.directory) / "target.wav"
    first.write_bytes(b"first output")
    (Path(allocated.directory) / "unrelated").mkdir()
    before = _snapshot(tree)
    validate_allocated_workspace(allocated)
    validate_allocated_workspace(allocated)
    (Path(allocated.directory) / "mix.wav").write_bytes(b"second output")
    validate_allocated_workspace(allocated)  # a second distinct output needs no reallocation
    after = _snapshot(tree)
    assert {key: after[key] for key in before} == before
    assert sorted(os.listdir(allocated.directory)) == ["mix.wav", "target.wav", "unrelated"]


def test_validation_does_not_depend_on_the_current_directory(tree, allocated, monkeypatch):
    monkeypatch.chdir(tree / "other")
    validate_allocated_workspace(allocated)
    assert not (tree / "other" / TOKEN).exists()


def test_protected_checks_resolve_freshly_with_each_configured_root(tree, allocated, monkeypatch):
    seen = []
    real = allocation.resolve_artifact_location

    def spy(artifact, *, root):
        seen.append((artifact, root))
        return real(artifact, root=root)

    monkeypatch.setattr(allocation, "resolve_artifact_location", spy)
    validate_allocated_workspace(allocated)
    expected = [(loc.artifact, loc.configured_root) for loc in allocated.request.protected_locations]
    assert seen == expected


def test_real_root_symlink_alias_used_at_allocation_still_validates(tree):
    _symlink(tree / "outputs", tree / "alias", directory=True)
    record = allocate_workspace(_request(tree, root_path=tree / "alias"))
    validate_allocated_workspace(record)


# --- request policy before filesystem access --------------------------------------------------


@pytest.mark.parametrize("bad", ["", "../x", "a/b", "a\\b", "a.b", "..", None])
def test_bypassed_token_is_rejected_before_filesystem_access(tree, allocated, monkeypatch, bad):
    object.__setattr__(allocated.request.workspace, "directory_token", bad)
    touched = _deny_all(monkeypatch, FILESYSTEM)
    _fail(allocated, Code.UNSUPPORTED_PATH)
    assert touched == []


@pytest.mark.skipif(not WINDOWS, reason="Windows reserved device names apply only on Windows")
def test_windows_reserved_token_is_rejected_before_filesystem_access(tree, allocated, monkeypatch):
    object.__setattr__(allocated.request.workspace, "directory_token", "CON")
    touched = _deny_all(monkeypatch, FILESYSTEM)
    _fail(allocated, Code.UNSUPPORTED_PATH)
    assert touched == []


def test_root_key_mismatch_is_rejected_before_filesystem_access(tree, allocated, monkeypatch):
    record = AllocatedWorkspace(_request(tree, root_key="other"), allocated.resolved_root, allocated.directory)
    touched = _deny_all(monkeypatch, FILESYSTEM)
    _fail(record, Code.ROOT_KEY_MISMATCH)
    assert touched == []


@pytest.mark.parametrize("omitted", ["stem-t1", "mix", "base-t2"], ids=["current-stem", "current-mix", "baseline"])
def test_omitted_mandatory_inventory_is_rejected_before_filesystem_access(tree, allocated, monkeypatch, omitted):
    protected = tuple(loc for loc in _inventory(tree) if loc.artifact.artifact_id != ArtifactId(omitted))
    record = AllocatedWorkspace(_request(tree, protected=protected), allocated.resolved_root, allocated.directory)
    touched = _deny_all(monkeypatch, FILESYSTEM)
    error = _fail(record, Code.INVALID_INVENTORY)
    assert omitted in str(error) and touched == []


def test_conflicting_inventory_metadata_is_rejected(tree, allocated):
    altered = dataclasses.replace(MIX, content_hash="1" * 64)
    protected = tuple(
        resolve_artifact_location(altered, root=str(tree / "sources")) if loc.artifact == MIX else loc
        for loc in _inventory(tree)
    )
    record = AllocatedWorkspace(_request(tree, protected=protected), allocated.resolved_root, allocated.directory)
    _fail(record, Code.INVALID_INVENTORY)


@pytest.mark.parametrize("root", ["relative", "file:///outputs", "//server/share", "r\x00"], ids=repr)
def test_unsupported_root_syntax_is_rejected_without_current_directory_fallback(tree, allocated, monkeypatch, root):
    monkeypatch.chdir(tree)
    record = AllocatedWorkspace(_request(tree, root_path=root), allocated.resolved_root, allocated.directory)
    _fail(record, Code.UNSUPPORTED_PATH)


# --- recorded root and directory ----------------------------------------------------------------


def test_stale_recorded_root_is_a_workspace_change(tree, allocated):
    record = dataclasses.replace(allocated, resolved_root=_canonical(tree / "other"))
    _fail(record, Code.WORKSPACE_CHANGED)


@pytest.mark.parametrize(
    "make_directory",
    [
        lambda t: str(Path(_canonical(t / "outputs")) / "exec-2"),
        lambda t: _canonical(t / "other"),
        lambda t: _canonical(t / "outputs"),
        lambda t: str(Path(_canonical(t / "outputs")) / TOKEN / "nested"),
        lambda t: str(Path(_canonical(t / "sources"))),
    ],
    ids=["sibling-token", "outside-root", "root-itself", "nested", "sources"],
)
def test_recorded_directory_other_than_the_assigned_child_is_a_workspace_change(tree, allocated, make_directory):
    (tree / "outputs" / "exec-2").mkdir()
    (Path(allocated.directory) / "nested").mkdir()
    record = dataclasses.replace(allocated, directory=make_directory(tree))
    _fail(record, Code.WORKSPACE_CHANGED)


def test_never_allocated_child_is_a_workspace_change_and_is_not_created(tree):
    request = _request(tree)
    record = AllocatedWorkspace(request, _canonical(tree / "outputs"), str(Path(_canonical(tree / "outputs")) / TOKEN))
    _fail(record, Code.WORKSPACE_CHANGED)
    assert not (tree / "outputs" / TOKEN).exists()


@pytest.mark.parametrize(
    "make_root",
    [lambda t: t / "missing", lambda t: t / "sources" / "mix.wav"],
    ids=["missing", "file"],
)
def test_invalid_configured_roots_keep_the_root_codes(tree, allocated, make_root):
    record = AllocatedWorkspace(_request(tree, root_path=make_root(tree)), allocated.resolved_root, allocated.directory)
    _fail(record, Code.INVALID_ROOT)


def test_real_root_removed_after_allocation_is_an_invalid_root(tree, allocated):
    shutil.rmtree(tree / "outputs")
    _fail(allocated, Code.INVALID_ROOT)


def test_real_root_redirected_by_a_symlink_is_a_workspace_change(tree, allocated):
    os.rename(tree / "outputs", tree / "moved")
    _symlink(tree / "other", tree / "outputs", directory=True)
    _fail(allocated, Code.WORKSPACE_CHANGED)
    assert (tree / "moved" / TOKEN).is_dir() and os.listdir(tree / "other") == []


def test_real_root_symlink_cycle_is_an_invalid_root(tree, allocated):
    _symlink(tree / "b", tree / "a", directory=True)
    _symlink(tree / "a", tree / "b", directory=True)
    record = AllocatedWorkspace(_request(tree, root_path=tree / "a"), allocated.resolved_root, allocated.directory)
    error = _fail(record, Code.INVALID_ROOT)
    assert error.__cause__ is not None


def test_real_child_removed_is_a_workspace_change(tree, allocated):
    os.rmdir(allocated.directory)
    _fail(allocated, Code.WORKSPACE_CHANGED)


def test_real_child_replaced_by_a_file_is_a_workspace_change_and_left_untouched(tree, allocated):
    os.rmdir(allocated.directory)
    Path(allocated.directory).write_bytes(b"x")
    _fail(allocated, Code.WORKSPACE_CHANGED)
    assert Path(allocated.directory).read_bytes() == b"x"


def test_real_child_replaced_by_a_symlink_is_a_workspace_change(tree, allocated):
    os.rmdir(allocated.directory)
    _symlink(tree / "other", allocated.directory, directory=True)
    _fail(allocated, Code.WORKSPACE_CHANGED)
    assert os.path.islink(allocated.directory) and os.listdir(tree / "other") == []


@pytest.mark.skipif(not WINDOWS, reason="directory junctions exist only on Windows")
def test_real_child_replaced_by_a_junction_is_a_workspace_change(tree, allocated):
    os.rmdir(allocated.directory)
    _junction(tree / "other", allocated.directory)
    _fail(allocated, Code.WORKSPACE_CHANGED)
    assert os.listdir(tree / "other") == []


def _resolve_double(monkeypatch, target: str, *, exc=None, result=None) -> list:
    calls = []

    def fake(self, strict=False):
        if str(self) == target:
            calls.append(strict)
            if exc is not None:
                raise exc
            return Path(result)
        return _REAL.resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", fake)
    return calls


def _lstat_double(monkeypatch, target: str, *, exc=None, result=None) -> list:
    calls = []

    def fake(path, *args, **kwargs):
        if os.fspath(path) == target:
            calls.append(path)
            if exc is not None:
                raise exc
            return result
        return _REAL.lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", fake)
    return calls


def _stat_double(monkeypatch, target: str, *, exc=None) -> list:
    calls = []

    def fake(path, *args, **kwargs):
        if os.fspath(path) == target:
            calls.append(path)
            raise exc
        return _REAL.stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fake)
    return calls


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_file_attributes=0),
        SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_file_attributes=0),
        SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT),
    ],
    ids=["symlink", "regular", "reparse-point"],
)
def test_child_must_lstat_as_a_plain_directory(tree, allocated, monkeypatch, result):
    calls = _lstat_double(monkeypatch, allocated.directory, result=result)
    _fail(allocated, Code.WORKSPACE_CHANGED)
    assert calls


# --- ELOOP and narrow error mapping (controlled doubles) -------------------------------------------

EXPECTED_RESOLUTION = [
    FileNotFoundError(errno.ENOENT, "missing"),
    NotADirectoryError(errno.ENOTDIR, "not a directory"),
    OSError(errno.ELOOP, "loop"),
    RuntimeError("Symlink loop from 'x'"),
]


@pytest.mark.parametrize("exc", EXPECTED_RESOLUTION, ids=_id)
def test_root_resolution_failures_are_invalid_roots_with_chaining(tree, allocated, monkeypatch, exc):
    calls = _resolve_double(monkeypatch, allocated.request.root.path, exc=exc)
    error = _fail(allocated, Code.INVALID_ROOT)
    assert calls == [True] and error.__cause__ is exc


def test_root_resolution_permission_denial_keeps_the_root_permission_code(tree, allocated, monkeypatch):
    exc = PermissionError(errno.EACCES, "denied")
    _resolve_double(monkeypatch, allocated.request.root.path, exc=exc)
    assert _fail(allocated, Code.NOT_WRITABLE).__cause__ is exc


@pytest.mark.parametrize(
    "exc, code",
    [
        (OSError(errno.ELOOP, "loop"), Code.INVALID_ROOT),
        (FileNotFoundError(errno.ENOENT, "gone"), Code.INVALID_ROOT),
        (PermissionError(errno.EACCES, "denied"), Code.NOT_WRITABLE),
    ],
    ids=_id,
)
def test_root_directory_inspection_errors_keep_the_root_codes(tree, allocated, monkeypatch, exc, code):
    calls = _stat_double(monkeypatch, allocated.resolved_root, exc=exc)
    assert _fail(allocated, code).__cause__ is exc and calls


@pytest.mark.parametrize("exc", EXPECTED_RESOLUTION + [PermissionError(errno.EACCES, "denied")], ids=_id)
def test_child_resolution_failures_are_workspace_changes(tree, allocated, monkeypatch, exc):
    calls = _resolve_double(monkeypatch, allocated.directory, exc=exc)
    error = _fail(allocated, Code.WORKSPACE_CHANGED)
    assert calls == [True] and error.__cause__ is exc


def test_child_resolving_elsewhere_is_a_workspace_change(tree, allocated, monkeypatch):
    _resolve_double(monkeypatch, allocated.directory, result=_canonical(tree / "other"))
    _fail(allocated, Code.WORKSPACE_CHANGED)


@pytest.mark.parametrize(
    "exc",
    [
        OSError(errno.ELOOP, "loop"),
        FileNotFoundError(errno.ENOENT, "gone"),
        NotADirectoryError(errno.ENOTDIR, "replaced"),
        PermissionError(errno.EACCES, "denied"),
    ],
    ids=_id,
)
def test_child_lstat_failures_are_workspace_changes(tree, allocated, monkeypatch, exc):
    calls = _lstat_double(monkeypatch, allocated.directory, exc=exc)
    error = _fail(allocated, Code.WORKSPACE_CHANGED)
    assert calls and error.__cause__ is exc


UNEXPECTED = [
    OSError(errno.EIO, "I/O"),
    OSError(errno.ENAMETOOLONG, "long"),
    MemoryError(),
    RecursionError("deep"),
    TypeError("programming error"),
]


@pytest.mark.parametrize("exc", UNEXPECTED, ids=_id)
@pytest.mark.parametrize("stage", ["root-resolve", "root-stat", "child-resolve", "child-lstat"])
def test_unexpected_inspection_errors_propagate_unchanged(tree, allocated, monkeypatch, stage, exc):
    if stage == "root-resolve":
        _resolve_double(monkeypatch, allocated.request.root.path, exc=exc)
    elif stage == "root-stat":
        _stat_double(monkeypatch, allocated.resolved_root, exc=exc)
    elif stage == "child-resolve":
        _resolve_double(monkeypatch, allocated.directory, exc=exc)
    else:
        _lstat_double(monkeypatch, allocated.directory, exc=exc)
    with pytest.raises(type(exc)) as info:
        validate_allocated_workspace(allocated)
    assert info.value is exc


# --- protected inventory ----------------------------------------------------------------------------


def test_protected_file_removed_after_allocation_is_a_protected_location_failure(tree, allocated):
    (tree / "sources" / "base-t2.wav").unlink()
    error = _fail(allocated, Code.PROTECTED_LOCATION)
    assert isinstance(error.__cause__, LocationResolutionError) and "base-t2" in str(error)


def test_protected_file_replaced_by_a_directory_is_not_a_regular_file(tree, allocated):
    (tree / "sources" / "mix.wav").unlink()
    (tree / "sources" / "mix.wav").mkdir()
    assert "regular" in str(_fail(allocated, Code.PROTECTED_LOCATION))


def test_stale_protected_record_is_rejected(tree, allocated):
    (tree / "sources" / "elsewhere.wav").write_bytes(b"x")
    protected = tuple(
        dataclasses.replace(loc, absolute_path=_canonical(tree / "sources" / "elsewhere.wav"))
        if loc.artifact == MIX
        else loc
        for loc in _inventory(tree)
    )
    record = AllocatedWorkspace(_request(tree, protected=protected), allocated.resolved_root, allocated.directory)
    assert "stale" in str(_fail(record, Code.PROTECTED_LOCATION))


def test_real_protected_symlink_cycle_is_a_protected_location_failure(tree, allocated):
    _symlink(tree / "sources" / "loop-b.wav", tree / "sources" / "loop-a.wav", directory=False)
    _symlink(tree / "sources" / "loop-a.wav", tree / "sources" / "loop-b.wav", directory=False)
    looped = dataclasses.replace(
        allocated.request.protected_locations[0],
        artifact=_artifact("loop", "loop-a.wav"),
        absolute_path=str(tree / "sources" / "loop-a.wav"),
    )
    request = _request(tree, protected=_inventory(tree) + (looped,))
    error = _fail(AllocatedWorkspace(request, allocated.resolved_root, allocated.directory), Code.PROTECTED_LOCATION)
    assert isinstance(error.__cause__, LocationResolutionError)


def test_protected_stat_link_cycle_keeps_the_existing_propagation_policy(tree, allocated, monkeypatch):
    # Existing allocation policy maps only missing, non-directory and permission failures of the
    # protected type check; the validator reuses that helper unchanged (controlled double).
    loop = OSError(errno.ELOOP, "loop")
    _stat_double(monkeypatch, allocated.request.protected_locations[1].absolute_path, exc=loop)
    with pytest.raises(OSError) as info:
        validate_allocated_workspace(allocated)
    assert info.value is loop


def test_protected_file_inside_the_child_is_an_overlap(tree, allocated):
    (Path(allocated.directory) / "kept.wav").write_bytes(b"protected")
    kept = resolve_artifact_location(_artifact("kept", f"{TOKEN}/kept.wav"), root=str(tree / "outputs"))
    request = _request(tree, protected=_inventory(tree) + (kept,))
    _fail(AllocatedWorkspace(request, allocated.resolved_root, allocated.directory), Code.PROTECTED_OVERLAP)
    assert (Path(allocated.directory) / "kept.wav").read_bytes() == b"protected"


def test_protected_resolver_errors_other_than_resolution_failures_propagate(tree, allocated, monkeypatch):
    exc = MemoryError()

    def fake(artifact, *, root):
        raise exc

    monkeypatch.setattr(allocation, "resolve_artifact_location", fake)
    with pytest.raises(MemoryError) as info:
        validate_allocated_workspace(allocated)
    assert info.value is exc
