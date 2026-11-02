"""Tests for fresh output workspace allocation below an explicit, existing output root.

Fixtures are directories and tiny placeholder files created in ``tmp_path``; no project media or
audio content is used. Native filesystem tests run on the host platform and name their scope. Real
symbolic links and (on Windows) directory junctions are used where the host can create them; such
tests skip with the host's reason otherwise. Errors the host cannot produce reliably (permission
denials, link cycles inside ``Path.resolve``, replaced entries between allocation steps) are
simulated with controlled doubles of ``Path.resolve``, ``Path.mkdir``, ``os.stat``, ``os.lstat`` or
the protected-location resolver; these doubles exercise branch mapping only and are not evidence of
real platform behaviour.
"""

import builtins
import dataclasses
import errno
import io
import os
import shutil
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import autonomous_mixing.application.execution.identity as identity
import autonomous_mixing.infrastructure.filesystem.locations as locations
import autonomous_mixing.infrastructure.workspaces as workspaces
import autonomous_mixing.infrastructure.workspaces.allocation as allocation
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
from autonomous_mixing.infrastructure.filesystem import (
    LocationResolutionError,
    LocationResolutionFailureCode,
    ResolvedArtifactLocation,
    resolve_artifact_location,
)
from autonomous_mixing.infrastructure.workspaces import (
    AllocatedWorkspace,
    WorkspaceAllocationError,
    WorkspaceAllocationFailureCode,
    WorkspaceAllocationRequest,
    WorkspaceRoot,
    allocate_workspace,
)

Code = WorkspaceAllocationFailureCode
TOKEN = "exec-1"
ROOT_KEY = "outputs"


# --- metadata fixtures (no filesystem access) -----------------------------------------------


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
BASE_T2 = _artifact("base-t2", "base-t2.wav", tracks=("t2",), revision="r0")  # baseline absent from audio


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


def _workspace(token=TOKEN, root_key=ROOT_KEY) -> ExecutionWorkspace:
    return ExecutionWorkspace(ExecutionId("e1"), root_key, token)


WINDOWS = os.name == "nt"
FAKE_ROOT = "C:\\src" if WINDOWS else "/src"


def _record(artifact, root=FAKE_ROOT):
    """A metadata-only location record (no filesystem access); its paths need not exist."""
    return ResolvedArtifactLocation(artifact, root, root, str(Path(root) / artifact.location))


# --- Task 1: records and failure surface ----------------------------------------------------


def test_workspace_root_keeps_its_fields_and_is_frozen():
    root = WorkspaceRoot(ROOT_KEY, "/any/path")
    assert (root.root_key, root.path) == (ROOT_KEY, "/any/path")
    with pytest.raises(dataclasses.FrozenInstanceError):
        root.path = "/other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "fields",
    [(None, "/p"), ("", "/p"), ("   ", "/p"), (1, "/p"), ("k", None), ("k", ""), ("k", " \t"), ("k", b"/p")],
    ids=repr,
)
def test_workspace_root_rejects_wrong_types_and_blank_text(fields):
    with pytest.raises(ValueError):
        WorkspaceRoot(*fields)


def test_workspace_root_rejects_path_objects():
    with pytest.raises(ValueError):
        WorkspaceRoot(ROOT_KEY, Path("/p"))  # type: ignore[arg-type]


def _request(protected=None, *, workspace=None, root=None, state=None) -> WorkspaceAllocationRequest:
    return WorkspaceAllocationRequest(
        workspace or _workspace(),
        root or WorkspaceRoot(ROOT_KEY, "/unused"),
        state or _state(),
        protected if protected is not None else (_record(STEM_T1), _record(MIX), _record(BASE_T2)),
    )


def test_request_retains_original_objects_and_is_frozen():
    workspace, root, state = _workspace(), WorkspaceRoot(ROOT_KEY, "/unused"), _state()
    protected = (_record(STEM_T1), _record(MIX), _record(BASE_T2))
    request = WorkspaceAllocationRequest(workspace, root, state, protected)
    assert request.workspace is workspace and request.root is root and request.state is state
    assert request.protected_locations is protected
    assert all(a is b for a, b in zip(request.protected_locations, protected, strict=True))
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.root = root  # type: ignore[misc]


@pytest.mark.parametrize("field", ["workspace", "root", "state", "protected_locations"])
@pytest.mark.parametrize("value", [None, "text", object()], ids=["none", "text", "object"])
def test_request_rejects_wrong_field_types(field, value):
    kwargs = {
        "workspace": _workspace(),
        "root": WorkspaceRoot(ROOT_KEY, "/unused"),
        "state": _state(),
        "protected_locations": (_record(STEM_T1),),
    }
    kwargs[field] = value
    with pytest.raises(ValueError):
        WorkspaceAllocationRequest(**kwargs)


def test_request_requires_a_tuple_of_resolved_locations():
    with pytest.raises(ValueError):
        _request([_record(STEM_T1)])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _request((_record(STEM_T1), STEM_T1))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _request((_record(STEM_T1), "/src/mix.wav"))  # type: ignore[arg-type]


def test_empty_protected_tuple_is_structurally_valid():
    assert _request(()).protected_locations == ()


def test_request_rejects_duplicate_identity_keys_even_when_records_are_equal():
    with pytest.raises(ValueError, match="duplicate"):
        _request((_record(STEM_T1), _record(STEM_T1)))
    moved = _artifact("stem-t1", "elsewhere.wav")
    with pytest.raises(ValueError, match="duplicate"):
        _request((_record(STEM_T1), _record(moved)))


def test_request_allows_the_same_artifact_id_in_another_project():
    foreign = _artifact("stem-t1", "stem-t1.wav", project="p2")
    request = _request((_record(STEM_T1), _record(foreign)))
    assert len(request.protected_locations) == 2


def test_request_allows_distinct_keys_that_share_one_physical_file():
    alias = _artifact("stem-alias", "stem-t1.wav")
    first, second = _record(STEM_T1), _record(alias)
    assert first.absolute_path == second.absolute_path
    request = _request((first, second))
    assert request.protected_locations == (first, second)


def test_record_constructors_perform_no_filesystem_access(monkeypatch):
    touched = _forbid_filesystem(monkeypatch)
    request = _request()
    WorkspaceRoot(ROOT_KEY, "/does/not/exist")
    AllocatedWorkspace(request, "/does/not/exist", "/does/not/exist/exec-1")
    assert touched == []


def test_allocated_workspace_is_a_descriptive_record_and_may_be_manual_or_stale():
    request = _request()
    record = AllocatedWorkspace(request, "/nowhere", "/nowhere/exec-1")
    assert record.request is request and record.resolved_root == "/nowhere"
    assert record.directory == "/nowhere/exec-1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.directory = "/x"  # type: ignore[misc]


@pytest.mark.parametrize(
    "fields",
    [(None, "/r", "/r/d"), ("request", "/r", "/r/d"), ("REQ", "", "/r/d"), ("REQ", "/r", " "), ("REQ", "/r", None)],
    ids=repr,
)
def test_allocated_workspace_rejects_wrong_types_and_blank_text(fields):
    request = _request()
    values = tuple(request if v == "REQ" else v for v in fields)
    with pytest.raises(ValueError):
        AllocatedWorkspace(*values)


def test_failure_codes_are_exactly_the_specified_members_with_lowercase_values():
    expected = [
        "ROOT_KEY_MISMATCH",
        "INVALID_INVENTORY",
        "INVALID_ROOT",
        "UNSUPPORTED_PATH",
        "PROTECTED_LOCATION",
        "PROTECTED_OVERLAP",
        "COLLISION",
        "NOT_WRITABLE",
        "WORKSPACE_CHANGED",
    ]
    assert [member.name for member in Code] == expected
    assert [member.value for member in Code] == [name.lower() for name in expected]


@pytest.mark.parametrize("code", list(Code), ids=lambda c: c.name)
def test_allocation_error_carries_code_prefix_and_no_created_directory_by_default(code):
    error = WorkspaceAllocationError(code, "details")
    assert isinstance(error, ValueError)
    assert error.code is code and error.created_directory is None
    assert str(error) == f"{code.value}: details"


def test_allocation_error_reports_a_known_created_directory_as_keyword_only():
    error = WorkspaceAllocationError(Code.WORKSPACE_CHANGED, "changed", created_directory="/r/exec-1")
    assert error.created_directory == "/r/exec-1"
    with pytest.raises(TypeError):
        WorkspaceAllocationError(Code.WORKSPACE_CHANGED, "changed", "/r/exec-1")  # type: ignore[misc]


# --- shared doubles ---------------------------------------------------------------------------


class _Forbidden(AssertionError):
    pass


def _forbid_filesystem(monkeypatch) -> list:
    """Make every filesystem entry point the allocator or resolver could use fail loudly."""
    touched = []

    def deny(name):
        def call(*args, **kwargs):
            touched.append(name)
            raise _Forbidden(f"filesystem access via {name}")

        return call

    for owner, name in [
        (Path, "resolve"),
        (Path, "mkdir"),
        (Path, "exists"),
        (Path, "is_dir"),
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
    ]:
        monkeypatch.setattr(owner, name, deny(f"{getattr(owner, '__name__', owner)}.{name}"))
    return touched


# --- Task 2: native fixtures and spies --------------------------------------------------------

# Real functions captured before any test double is installed; injected test actions use these.
_REAL = SimpleNamespace(
    rmdir=os.rmdir, rename=os.rename, symlink=os.symlink, open=builtins.open, resolve=Path.resolve, stat=os.stat
)
SOURCES = ("stem-t1.wav", "mix.wav", "base-t2.wav", "retained.wav")
RETAINED = _artifact("retained", "retained.wav", tracks=("t1", "t2"), kind=ArtifactKind.MIX, revision="r1")
FOREIGN = _artifact("stem-t1", "foreign.wav", project="p2")  # same artifact ID, other project


@pytest.fixture
def tree(tmp_path):
    """``sources`` with tiny placeholder files, ``foreign`` for another project, and ``outputs``."""
    sources = tmp_path / "sources"
    sources.mkdir()
    for name in SOURCES:
        (sources / name).write_bytes(b"fixture")
    (tmp_path / "foreign").mkdir()
    (tmp_path / "foreign" / "foreign.wav").write_bytes(b"fixture")
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "earlier.txt").write_bytes(b"keep")
    return tmp_path


def _inventory(tree, *extra) -> tuple:
    return tuple(resolve_artifact_location(a, root=str(tree / "sources")) for a in (STEM_T1, MIX, BASE_T2, *extra))


def _real_request(tree, *, token=TOKEN, protected=None, root_path=None, root_key=ROOT_KEY):
    return WorkspaceAllocationRequest(
        _workspace(token),
        WorkspaceRoot(root_key, str(root_path if root_path is not None else tree / "outputs")),
        _state(),
        protected if protected is not None else _inventory(tree),
    )


def _canonical(path) -> str:
    return str(_REAL.resolve(Path(path), strict=True))


def _expected_directory(tree, token=TOKEN) -> str:
    return str(Path(_canonical(tree / "outputs")) / token)


def _fail(request, code) -> WorkspaceAllocationError:
    with pytest.raises(WorkspaceAllocationError) as info:
        allocate_workspace(request)
    assert info.value.code is code, info.value
    assert str(info.value).startswith(f"{code.value}: ")
    return info.value


def _is_link(st) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _snapshot(base: Path) -> dict:
    """Entry type, link target, regular-file bytes and file mtimes below ``base``, not following links."""
    entries = {}
    pending = [base]
    while pending:
        with os.scandir(pending.pop()) as listing:
            names = sorted(entry.path for entry in listing)
        for path in names:
            st = os.lstat(path)
            if _is_link(st):
                entries[path] = ("link", os.readlink(path))
            elif stat.S_ISDIR(st.st_mode):
                entries[path] = ("dir",)
                pending.append(Path(path))
            else:
                with _REAL.open(path, "rb") as handle:
                    entries[path] = ("file", handle.read(), st.st_mtime_ns)
    return entries


MUTATORS = [
    (os, "remove"),
    (os, "unlink"),
    (os, "rmdir"),
    (os, "removedirs"),
    (os, "rename"),
    (os, "renames"),
    (os, "replace"),
    (os, "makedirs"),
    (os, "truncate"),
    (os, "open"),
    (shutil, "rmtree"),
    (shutil, "move"),
    (Path, "unlink"),
    (Path, "rmdir"),
    (Path, "rename"),
    (Path, "replace"),
    (Path, "touch"),
    (Path, "write_bytes"),
    (Path, "write_text"),
    (Path, "open"),
    (Path, "read_bytes"),
    (Path, "read_text"),
    (builtins, "open"),
    (io, "open"),
]


def _forbid_mutation_and_content(monkeypatch) -> list:
    """Deny deletion, renaming, recursive removal, parent creation and every content open."""
    calls = []

    def deny(name):
        def call(*args, **kwargs):
            calls.append(name)
            raise _Forbidden(name)

        return call

    for owner, name in MUTATORS:
        monkeypatch.setattr(owner, name, deny(f"{owner.__name__}.{name}"))
    return calls


class _Mkdir:
    """Records ``Path.mkdir``/``os.mkdir`` calls, performs the real creation, then runs ``after``."""

    def __init__(self, monkeypatch, after=None, fail=None):
        self.path_calls, self.os_calls, self.created = [], [], False
        real_path_mkdir, real_os_mkdir = Path.mkdir, os.mkdir

        def path_mkdir(path, mode=0o777, parents=False, exist_ok=False):
            self.path_calls.append((str(path), parents, exist_ok))
            if fail is not None:
                raise fail
            real_path_mkdir(path, mode, parents, exist_ok)
            self.created = True
            if after is not None:
                after(path)

        def os_mkdir(path, mode=0o777, *, dir_fd=None):
            self.os_calls.append(os.fspath(path))
            return real_os_mkdir(path, mode, dir_fd=dir_fd)

        monkeypatch.setattr(Path, "mkdir", path_mkdir)
        monkeypatch.setattr(os, "mkdir", os_mkdir)


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


def _id(value):
    if isinstance(value, BaseException):
        return f"{type(value).__name__}-{getattr(value, 'errno', '')}"
    return repr(value)


UNEXPECTED = [
    OSError(errno.EIO, "I/O error"),
    MemoryError(),
    RecursionError("maximum recursion depth exceeded"),
    TypeError("programming error"),
    KeyboardInterrupt(),
]


# --- public surface ---------------------------------------------------------------------------


def test_public_interface_exports_exactly_the_six_definitions():
    expected = {
        "WorkspaceRoot",
        "WorkspaceAllocationRequest",
        "AllocatedWorkspace",
        "WorkspaceAllocationFailureCode",
        "WorkspaceAllocationError",
        "allocate_workspace",
    }
    assert set(workspaces.__all__) == expected and len(workspaces.__all__) == 6
    for name in expected:
        assert getattr(workspaces, name) is getattr(allocation, name)


def test_allocator_rejects_a_non_request_as_a_plain_argument_error(monkeypatch):
    touched = _forbid_filesystem(monkeypatch)
    with pytest.raises(ValueError) as info:
        allocate_workspace("request")  # type: ignore[arg-type]
    assert type(info.value) is ValueError and touched == []


# --- success ----------------------------------------------------------------------------------


def test_allocation_creates_exactly_one_empty_direct_child_and_keeps_the_request(tree, monkeypatch):
    request = _real_request(tree)
    before = _snapshot(tree)
    forbidden = _forbid_mutation_and_content(monkeypatch)
    mkdir = _Mkdir(monkeypatch)
    result = allocate_workspace(request)
    monkeypatch.undo()
    assert forbidden == []
    assert mkdir.path_calls == [(result.directory, False, False)] and len(mkdir.os_calls) == 1
    assert isinstance(result, AllocatedWorkspace) and result.request is request
    assert result.request.protected_locations is request.protected_locations
    assert result.resolved_root == _canonical(tree / "outputs")
    assert result.directory == _expected_directory(tree)
    assert Path(result.directory).is_absolute() and Path(result.directory).parent == Path(result.resolved_root)
    assert os.listdir(result.directory) == []
    after = _snapshot(tree)
    assert set(after) - set(before) == {str(tree / "outputs" / TOKEN)}
    assert {key: after[key] for key in before} == before  # protected bytes and root entries unchanged


def test_additional_retained_and_cross_project_entries_are_accepted(tree):
    foreign = resolve_artifact_location(FOREIGN, root=str(tree / "foreign"))
    request = _real_request(tree, protected=_inventory(tree, RETAINED) + (foreign,))
    result = allocate_workspace(request)
    assert result.request.protected_locations[-1] is foreign


def test_inventory_order_and_shared_physical_files_do_not_matter(tree):
    alias = resolve_artifact_location(_artifact("alias", "stem-t1.wav"), root=str(tree / "sources"))
    protected = tuple(reversed(_inventory(tree))) + (alias,)
    assert allocate_workspace(_real_request(tree, protected=protected)).directory == _expected_directory(tree)


def test_distinct_tokens_coexist_below_one_root(tree):
    first = allocate_workspace(_real_request(tree, token="exec-a"))
    second = allocate_workspace(_real_request(tree, token="exec-b"))
    assert first.directory != second.directory
    assert sorted(os.listdir(tree / "outputs")) == ["earlier.txt", "exec-a", "exec-b"]


def test_allocation_does_not_depend_on_the_current_directory(tree, monkeypatch):
    monkeypatch.chdir(tree / "sources")
    result = allocate_workspace(_real_request(tree))
    assert result.directory == _expected_directory(tree)
    assert not (tree / "sources" / TOKEN).exists()


# --- token and request precedence (before any filesystem access) ------------------------------


@pytest.mark.parametrize("bad", ["", "../x", "a/b", "a\\b", "\u00fc", "a.b", " a", "a\n", "..", ".", "C:x", None])
def test_defensive_token_recheck_rejects_bypassed_tokens_before_filesystem_access(tree, monkeypatch, bad):
    request = _real_request(tree)
    object.__setattr__(request.workspace, "directory_token", bad)
    touched = _forbid_filesystem(monkeypatch)
    _fail(request, Code.UNSUPPORTED_PATH)
    assert touched == []


def test_token_pattern_matches_the_application_assignment_pattern():
    assert allocation._DIRECTORY_TOKEN.pattern == identity._DIRECTORY_TOKEN.pattern


WINDOWS_RESERVED = ["CON", "con", "PRN", "Aux", "NUL", "nul", "COM1", "com9", "LPT1", "lpt9"]
NOT_RESERVED = ["CON_", "COM0", "LPT0", "nul-1", "console", "auxiliary", "exec-1", "COM10"]


@pytest.mark.parametrize("token", WINDOWS_RESERVED)
def test_token_helper_rejects_windows_device_names_only_under_the_windows_policy(token):
    assert allocation._token_problem(token, windows=True) is not None
    assert allocation._token_problem(token, windows=False) is None


@pytest.mark.parametrize("token", NOT_RESERVED)
def test_token_helper_accepts_ordinary_names_on_both_policies(token):
    assert allocation._token_problem(token, windows=True) is None
    assert allocation._token_problem(token, windows=False) is None


@pytest.mark.skipif(not WINDOWS, reason="Windows reserved device names apply only on Windows")
@pytest.mark.parametrize("token", WINDOWS_RESERVED)
def test_native_windows_reserved_tokens_are_rejected_before_filesystem_access(tree, monkeypatch, token):
    request = _real_request(tree, token=token)
    touched = _forbid_filesystem(monkeypatch)
    _fail(request, Code.UNSUPPORTED_PATH)
    assert touched == []


@pytest.mark.skipif(WINDOWS, reason="POSIX hosts impose no Windows device-name restriction")
def test_native_posix_accepts_windows_device_names_as_ordinary_tokens(tree):
    assert allocate_workspace(_real_request(tree, token="CON")).directory.endswith("CON")


def test_root_key_mismatch_precedes_inventory_and_root_access(tree, monkeypatch):
    request = _real_request(tree, protected=(), root_key="other", root_path="relative")
    touched = _forbid_filesystem(monkeypatch)
    _fail(request, Code.ROOT_KEY_MISMATCH)
    assert touched == []


def test_token_check_precedes_root_key_mismatch(tree, monkeypatch):
    request = _real_request(tree, protected=(), root_key="other")
    object.__setattr__(request.workspace, "directory_token", "../x")
    touched = _forbid_filesystem(monkeypatch)
    _fail(request, Code.UNSUPPORTED_PATH)
    assert touched == []


# --- mandatory inventory ----------------------------------------------------------------------


@pytest.mark.parametrize("omitted", ["stem-t1", "mix", "base-t2"], ids=["current-stem", "current-mix", "baseline"])
def test_missing_mandatory_entry_is_invalid_inventory_before_root_access(tree, monkeypatch, omitted):
    protected = tuple(loc for loc in _inventory(tree) if loc.artifact.artifact_id != ArtifactId(omitted))
    request = _real_request(tree, protected=protected, root_path="relative")
    touched = _forbid_filesystem(monkeypatch)
    error = _fail(request, Code.INVALID_INVENTORY)
    assert omitted in str(error) and touched == []
    monkeypatch.undo()
    assert not (tree / "outputs" / TOKEN).exists()


def test_baseline_absent_from_current_audio_is_still_mandatory():
    state = _state()
    assert state.audio.artifact_for(BASE_T2.artifact_id) is None
    assert BASE_T2 in [gain.input_artifact for gain in state.processing.track_gains]


@pytest.mark.parametrize(
    "changed",
    [{"content_hash": "1" * 64}, {"frame_count": 5}, {"revision": Revision("r9")}],
    ids=["hash", "frames", "revision"],
)
def test_conflicting_metadata_is_invalid_inventory_before_root_access(tree, monkeypatch, changed):
    altered = dataclasses.replace(MIX, **changed)
    protected = tuple(
        resolve_artifact_location(altered, root=str(tree / "sources")) if loc.artifact == MIX else loc
        for loc in _inventory(tree)
    )
    request = _real_request(tree, protected=protected)
    touched = _forbid_filesystem(monkeypatch)
    error = _fail(request, Code.INVALID_INVENTORY)
    assert "mix" in str(error) and touched == []


def test_empty_protected_tuple_fails_coverage_for_a_state_with_artifacts(tree, monkeypatch):
    request = _real_request(tree, protected=())
    touched = _forbid_filesystem(monkeypatch)
    _fail(request, Code.INVALID_INVENTORY)
    assert touched == []


def test_cross_project_entry_does_not_satisfy_a_mandatory_key(tree):
    foreign_mix = resolve_artifact_location(
        _artifact("mix", "foreign.wav", project="p2", tracks=("t1", "t2"), kind=ArtifactKind.MIX, revision="r2"),
        root=str(tree / "foreign"),
    )
    protected = tuple(loc for loc in _inventory(tree) if loc.artifact != MIX) + (foreign_mix,)
    _fail(_real_request(tree, protected=protected), Code.INVALID_INVENTORY)


def test_inventory_failure_precedes_root_syntax(tree):
    _fail(_real_request(tree, protected=(), root_path="relative"), Code.INVALID_INVENTORY)


# --- root policy --------------------------------------------------------------------------------

# Root forms checked for parity with the input resolver's root policy (require_absolute=True), on
# both flavours; the allocator implements the policy locally and must agree on accept/reject.
ROOT_FORMS = [
    "C:\\root",
    "C:/root/sub",
    "d:\\",
    "C:\\Root With Spaces\\\u00dcn\u00efc\u00f8d\u00e9",
    "C:\\root\\.\\sub",
    "C:\\root\\a..b",
    "/root",
    "/",
    "/root/./sub",
    "/dir with spaces/\u00fcn\u00efc\u00f8d\u00e9",
    "/root\\sub",
    "/root/..\\x",
    "root",
    "outputs\\root",
    ".",
    "root/sub",
    "C:",
    "C:root",
    "\\root",
    "/root\x00",
    "C:\\root:stream",
    "C:\\root\\..\\other",
    "/root/../other",
    "..",
    "\\\\server\\share\\root",
    "//server/share/root",
    "\\\\?\\C:\\root",
    "\\\\.\\C:\\root",
    "/\\server",
    "file:///C:/root",
    "FILE:///tmp/root",
    "s3://bucket",
    "C:/root",
    "c:/x",
]


@pytest.mark.parametrize("windows", [True, False], ids=["windows", "posix"])
@pytest.mark.parametrize("text", ROOT_FORMS, ids=repr)
def test_root_syntax_helper_matches_the_input_resolver_root_policy(text, windows):
    ours = allocation._root_syntax_problem(text, windows=windows)
    theirs = locations._lexical_problem(text, windows=windows, require_absolute=True)
    assert (ours is None) == (theirs is None), (ours, theirs)


NATIVE_ROOT_REJECTED = ["relative", "./outputs", "file:///outputs", "\\\\server\\share", "//server/share", "r\x00"] + (
    ["\\outputs", "/outputs", "C:outputs", "C:\\outputs:stream", "C:\\x\\..\\outputs"]
    if WINDOWS
    else ["C:\\outputs", "C:/outputs", "/x/../outputs"]
)


@pytest.mark.parametrize("root", NATIVE_ROOT_REJECTED, ids=repr)
def test_native_unsupported_roots_are_rejected_before_filesystem_access(tree, monkeypatch, root):
    request = _real_request(tree, root_path=root)
    touched = _forbid_filesystem(monkeypatch)
    _fail(request, Code.UNSUPPORTED_PATH)
    assert touched == []


def test_relative_root_has_no_current_directory_fallback(tree, monkeypatch):
    monkeypatch.chdir(tree)
    _fail(_real_request(tree, root_path="outputs"), Code.UNSUPPORTED_PATH)
    assert not (tree / "outputs" / TOKEN).exists()


@pytest.mark.parametrize(
    "make_root",
    [
        lambda t: t / "missing",
        lambda t: t / "missing-parent" / "outputs",
        lambda t: t / "sources" / "mix.wav",
        lambda t: t / "sources" / "mix.wav" / "below",
    ],
    ids=["missing", "missing-parent", "file", "below-file"],
)
def test_invalid_roots_are_rejected_without_creating_anything(tree, make_root):
    before = _snapshot(tree)
    _fail(_real_request(tree, root_path=make_root(tree)), Code.INVALID_ROOT)
    assert _snapshot(tree) == before


def _resolve_double(monkeypatch, target: str, *, when, exc=None, result=None) -> list:
    """Replace ``Path.resolve`` for the exact path string ``target`` while ``when()`` is true."""
    calls = []

    def fake(self, strict=False):
        if str(self) == target and when():
            calls.append(strict)
            if exc is not None:
                raise exc
            return Path(result)
        return _REAL.resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", fake)
    return calls


@pytest.mark.parametrize(
    "exc, code",
    [
        (FileNotFoundError(errno.ENOENT, "missing"), Code.INVALID_ROOT),
        (NotADirectoryError(errno.ENOTDIR, "not a directory"), Code.INVALID_ROOT),
        (OSError(errno.ELOOP, "loop"), Code.INVALID_ROOT),
        (RuntimeError("Symlink loop from 'x'"), Code.INVALID_ROOT),
        (PermissionError(errno.EACCES, "denied"), Code.NOT_WRITABLE),
    ],
    ids=_id,
)
def test_root_resolution_errors_map_narrowly_and_chain(tree, monkeypatch, exc, code):
    request = _real_request(tree)
    calls = _resolve_double(monkeypatch, request.root.path, when=lambda: True, exc=exc)
    mkdir = _Mkdir(monkeypatch)
    error = _fail(request, code)
    assert error.__cause__ is exc and error.created_directory is None
    assert calls == [True] and mkdir.path_calls == []


@pytest.mark.parametrize("exc", UNEXPECTED + [OSError(errno.ENAMETOOLONG, "long")], ids=_id)
def test_unexpected_root_resolution_errors_propagate_unchanged(tree, monkeypatch, exc):
    request = _real_request(tree)
    _resolve_double(monkeypatch, request.root.path, when=lambda: True, exc=exc)
    mkdir = _Mkdir(monkeypatch)
    with pytest.raises(type(exc)) as info:
        allocate_workspace(request)
    assert info.value is exc and mkdir.path_calls == []


def _stat_double(monkeypatch, target: str, *, exc=None, result=None) -> list:
    calls = []

    def fake(path, *args, **kwargs):
        if os.fspath(path) == target:
            calls.append(path)
            if exc is not None:
                raise exc
            return result
        return _REAL.stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fake)
    return calls


@pytest.mark.parametrize(
    "exc, code",
    [
        (FileNotFoundError(errno.ENOENT, "vanished"), Code.INVALID_ROOT),
        (NotADirectoryError(errno.ENOTDIR, "replaced"), Code.INVALID_ROOT),
        (PermissionError(errno.EACCES, "denied"), Code.NOT_WRITABLE),
    ],
    ids=_id,
)
def test_root_directory_inspection_maps_its_errors(tree, monkeypatch, exc, code):
    calls = _stat_double(monkeypatch, _canonical(tree / "outputs"), exc=exc)
    error = _fail(_real_request(tree), code)
    assert calls and error.__cause__ is exc


UNRELATED_INSPECTION = [
    OSError(errno.EIO, "I/O"),
    OSError(errno.ENAMETOOLONG, "long"),
    MemoryError(),
    TypeError("programming error"),
]


@pytest.mark.parametrize("exc", UNRELATED_INSPECTION, ids=_id)
def test_root_directory_inspection_propagates_unexpected_errors(tree, monkeypatch, exc):
    _stat_double(monkeypatch, _canonical(tree / "outputs"), exc=exc)
    mkdir = _Mkdir(monkeypatch)
    with pytest.raises(type(exc)) as info:
        allocate_workspace(_real_request(tree))
    assert info.value is exc and mkdir.path_calls == []


def test_root_directory_inspection_link_cycle_is_an_invalid_root(tree, monkeypatch):
    # Controlled: ELOOP from the root directory check before creation follows the root-cycle policy.
    loop = OSError(errno.ELOOP, "loop")
    calls = _stat_double(monkeypatch, _canonical(tree / "outputs"), exc=loop)
    mkdir = _Mkdir(monkeypatch)
    error = _fail(_real_request(tree), Code.INVALID_ROOT)
    monkeypatch.undo()
    assert calls and error.__cause__ is loop and error.created_directory is None
    assert mkdir.path_calls == [] and mkdir.os_calls == []
    assert not (tree / "outputs" / TOKEN).exists()


def _install_post_inspection_failure(monkeypatch, tree, boundary, exc, mkdir):
    """Fail one inspection boundary after this call's mkdir returned (controlled double)."""
    if boundary == "root-stat":
        root, real = _canonical(tree / "outputs"), os.stat

        def fake_stat(path, *args, **kwargs):
            if os.fspath(path) == root and mkdir.created:
                raise exc
            return real(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", fake_stat)
    elif boundary == "lstat":
        _lstat_double(monkeypatch, _expected_directory(tree), when=_created(mkdir), exc=exc)
    else:
        _scandir_double(monkeypatch, _expected_directory(tree), exc)


POST_BOUNDARIES = ["root-stat", "lstat", "scandir"]


@pytest.mark.parametrize("boundary", POST_BOUNDARIES)
def test_post_creation_link_cycle_is_a_workspace_change_that_retains_the_directory(tree, monkeypatch, boundary):
    loop = OSError(errno.ELOOP, "loop")
    request = _real_request(tree)
    forbidden = _forbid_mutation_and_content(monkeypatch)
    mkdir = _Mkdir(monkeypatch)
    _install_post_inspection_failure(monkeypatch, tree, boundary, loop, mkdir)
    error = _fail(request, Code.WORKSPACE_CHANGED)
    monkeypatch.undo()
    assert error.__cause__ is loop and error.created_directory == _expected_directory(tree)
    assert forbidden == [] and len(mkdir.os_calls) == 1
    assert Path(error.created_directory).is_dir() and os.listdir(error.created_directory) == []


@pytest.mark.parametrize("exc", UNRELATED_INSPECTION, ids=_id)
@pytest.mark.parametrize("boundary", POST_BOUNDARIES)
def test_post_creation_unrelated_inspection_errors_propagate_and_retain_the_directory(tree, monkeypatch, boundary, exc):
    request = _real_request(tree)
    forbidden = _forbid_mutation_and_content(monkeypatch)
    mkdir = _Mkdir(monkeypatch)
    _install_post_inspection_failure(monkeypatch, tree, boundary, exc, mkdir)
    with pytest.raises(type(exc)) as info:
        allocate_workspace(request)
    monkeypatch.undo()
    assert info.value is exc and not isinstance(info.value, WorkspaceAllocationError)
    assert forbidden == [] and Path(_expected_directory(tree)).is_dir()


def test_real_root_symlink_alias_records_the_actual_directory(tree):
    _symlink(tree / "outputs", tree / "alias", directory=True)
    result = allocate_workspace(_real_request(tree, root_path=tree / "alias"))
    assert result.request.root.path == str(tree / "alias")
    assert result.resolved_root == _canonical(tree / "outputs")
    assert result.directory == _expected_directory(tree)
    assert (tree / "outputs" / TOKEN).is_dir()


@pytest.mark.skipif(not WINDOWS, reason="directory junctions exist only on Windows")
def test_real_root_junction_alias_records_the_actual_directory(tree):
    _junction(tree / "outputs", tree / "junction")
    result = allocate_workspace(_real_request(tree, root_path=tree / "junction"))
    assert result.resolved_root == _canonical(tree / "outputs")
    assert result.directory == _expected_directory(tree)


def test_real_root_symlink_cycle_is_an_invalid_root(tree):
    _symlink(tree / "b", tree / "a", directory=True)
    _symlink(tree / "a", tree / "b", directory=True)
    error = _fail(_real_request(tree, root_path=tree / "a"), Code.INVALID_ROOT)
    assert error.__cause__ is not None


def test_real_dangling_root_symlink_is_an_invalid_root(tree):
    _symlink(tree / "missing", tree / "dangling", directory=True)
    _fail(_real_request(tree, root_path=tree / "dangling"), Code.INVALID_ROOT)
    assert not (tree / "missing").exists()


# --- protected locations ------------------------------------------------------------------------


def _replace_location(tree, artifact_id, **fields):
    return tuple(
        dataclasses.replace(loc, **fields) if loc.artifact.artifact_id == ArtifactId(artifact_id) else loc
        for loc in _inventory(tree)
    )


def test_missing_protected_file_is_a_protected_location_failure_with_chaining(tree, monkeypatch):
    request = _real_request(tree)
    (tree / "sources" / "base-t2.wav").unlink()
    mkdir = _Mkdir(monkeypatch)
    error = _fail(request, Code.PROTECTED_LOCATION)
    assert isinstance(error.__cause__, LocationResolutionError)
    assert error.__cause__.code is LocationResolutionFailureCode.NOT_FOUND
    assert "base-t2" in str(error) and mkdir.path_calls == []


def test_stale_absolute_path_is_rejected(tree):
    protected = _replace_location(tree, "mix", absolute_path=_canonical(tree / "sources" / "retained.wav"))
    error = _fail(_real_request(tree, protected=protected), Code.PROTECTED_LOCATION)
    assert "mix" in str(error) and "stale" in str(error)
    assert not (tree / "outputs" / TOKEN).exists()


def test_stale_resolved_root_is_rejected(tree):
    protected = _replace_location(tree, "stem-t1", resolved_root=_canonical(tree / "foreign"))
    _fail(_real_request(tree, protected=protected), Code.PROTECTED_LOCATION)


def test_directory_target_is_not_a_regular_protected_file(tree):
    (tree / "sources" / "folder").mkdir()
    folder = resolve_artifact_location(_artifact("folder", "folder"), root=str(tree / "sources"))
    error = _fail(_real_request(tree, protected=_inventory(tree) + (folder,)), Code.PROTECTED_LOCATION)
    assert "regular" in str(error) and not (tree / "outputs" / TOKEN).exists()


def test_additional_entries_receive_the_same_checks(tree):
    foreign = resolve_artifact_location(FOREIGN, root=str(tree / "foreign"))
    (tree / "foreign" / "foreign.wav").unlink()
    _fail(_real_request(tree, protected=_inventory(tree) + (foreign,)), Code.PROTECTED_LOCATION)


def test_real_dangling_protected_symlink_is_rejected(tree):
    (tree / "sources" / "gone.wav").write_bytes(b"x")
    _symlink(tree / "sources" / "gone.wav", tree / "sources" / "dangling.wav", directory=False)
    record = resolve_artifact_location(_artifact("dangling", "dangling.wav"), root=str(tree / "sources"))
    (tree / "sources" / "gone.wav").unlink()
    _fail(_real_request(tree, protected=_inventory(tree) + (record,)), Code.PROTECTED_LOCATION)
    assert os.path.islink(tree / "sources" / "dangling.wav")


def test_entries_are_checked_in_tuple_order(tree):
    stale = _replace_location(tree, "mix", absolute_path=_canonical(tree / "sources" / "retained.wav"))
    (tree / "sources" / "base-t2.wav").unlink()
    first = _fail(_real_request(tree, protected=stale), Code.PROTECTED_LOCATION)
    assert "mix" in str(first) and first.__cause__ is None
    second = _fail(_real_request(tree, protected=stale[::-1]), Code.PROTECTED_LOCATION)
    assert "base-t2" in str(second) and isinstance(second.__cause__, LocationResolutionError)


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError(errno.ENOENT, "vanished"),
        NotADirectoryError(errno.ENOTDIR, "replaced"),
        PermissionError(errno.EACCES, "denied"),
    ],
    ids=_id,
)
def test_protected_type_inspection_maps_expected_errors(tree, monkeypatch, exc):
    request = _real_request(tree)
    calls = _stat_double(monkeypatch, request.protected_locations[1].absolute_path, exc=exc)
    error = _fail(request, Code.PROTECTED_LOCATION)
    assert calls and error.__cause__ is exc and not (tree / "outputs" / TOKEN).exists()


@pytest.mark.parametrize("exc", [OSError(errno.EIO, "I/O"), MemoryError(), TypeError("bug")], ids=_id)
def test_protected_type_inspection_propagates_unexpected_errors(tree, monkeypatch, exc):
    request = _real_request(tree)
    _stat_double(monkeypatch, request.protected_locations[1].absolute_path, exc=exc)
    with pytest.raises(type(exc)) as info:
        allocate_workspace(request)
    assert info.value is exc and not (tree / "outputs" / TOKEN).exists()


@pytest.mark.parametrize(
    "exc",
    UNEXPECTED + [ValueError("argument bug"), WorkspaceAllocationError(Code.COLLISION, "injected")],
    ids=_id,
)
def test_protected_resolver_errors_other_than_resolution_failures_propagate(tree, monkeypatch, exc):
    request = _real_request(tree)

    def fake(artifact, *, root):
        raise exc

    monkeypatch.setattr(allocation, "resolve_artifact_location", fake)
    with pytest.raises(type(exc)) as info:
        allocate_workspace(request)
    assert info.value is exc and not (tree / "outputs" / TOKEN).exists()


def test_protected_checks_resolve_freshly_with_the_configured_root(tree, monkeypatch):
    seen = []
    real = allocation.resolve_artifact_location

    def spy(artifact, *, root):
        seen.append((artifact, root))
        return real(artifact, root=root)

    monkeypatch.setattr(allocation, "resolve_artifact_location", spy)
    request = _real_request(tree)
    allocate_workspace(request)
    assert [(loc.artifact, loc.configured_root) for loc in request.protected_locations] == seen
    assert all(a is loc.artifact for (a, _), loc in zip(seen, request.protected_locations, strict=True))


@pytest.mark.parametrize("failure", ["missing", "stale", "directory", "overlap"])
def test_rejected_preconditions_open_no_content_and_mutate_nothing(tree, monkeypatch, failure):
    protected = _inventory(tree)
    code = Code.PROTECTED_LOCATION
    if failure == "missing":
        (tree / "sources" / "mix.wav").unlink()
    elif failure == "stale":
        protected = _replace_location(tree, "mix", absolute_path=_canonical(tree / "sources" / "retained.wav"))
    elif failure == "directory":
        (tree / "sources" / "folder").mkdir()
        protected += (resolve_artifact_location(_artifact("folder", "folder"), root=str(tree / "sources")),)
    else:
        (tree / "outputs" / TOKEN).write_bytes(b"protected")
        protected += (resolve_artifact_location(_artifact("out", TOKEN), root=str(tree / "outputs")),)
        code = Code.PROTECTED_OVERLAP
    request = _real_request(tree, protected=protected)
    before = _snapshot(tree)
    forbidden = _forbid_mutation_and_content(monkeypatch)
    mkdir = _Mkdir(monkeypatch)
    _fail(request, code)
    monkeypatch.undo()
    assert forbidden == [] and mkdir.path_calls == [] and mkdir.os_calls == []
    assert _snapshot(tree) == before


# --- overlap ------------------------------------------------------------------------------------


def test_protected_file_at_the_planned_directory_is_an_overlap_not_a_collision(tree):
    (tree / "outputs" / TOKEN).write_bytes(b"protected")
    record = resolve_artifact_location(_artifact("out", TOKEN), root=str(tree / "outputs"))
    error = _fail(_real_request(tree, protected=_inventory(tree) + (record,)), Code.PROTECTED_OVERLAP)
    assert error.created_directory is None
    assert (tree / "outputs" / TOKEN).read_bytes() == b"protected"


def test_protected_file_below_the_planned_directory_is_an_overlap(tree):
    (tree / "outputs" / TOKEN).mkdir()
    (tree / "outputs" / TOKEN / "kept.wav").write_bytes(b"protected")
    record = resolve_artifact_location(_artifact("kept", f"{TOKEN}/kept.wav"), root=str(tree / "outputs"))
    _fail(_real_request(tree, protected=_inventory(tree) + (record,)), Code.PROTECTED_OVERLAP)
    assert os.listdir(tree / "outputs" / TOKEN) == ["kept.wav"]


def test_protected_file_above_the_planned_directory_is_an_overlap(tree, monkeypatch):
    # Controlled: a regular file cannot be an ancestor of the planned directory in an unchanged tree.
    ancestor = str(Path(_canonical(tree / "outputs")).parent)
    record = ResolvedArtifactLocation(_artifact("odd", "odd.wav"), str(tree), ancestor, ancestor)
    request = _real_request(tree, protected=_inventory(tree) + (record,))
    real = allocation.resolve_artifact_location

    def fake(artifact, *, root):
        return record if artifact is record.artifact else real(artifact, root=root)

    monkeypatch.setattr(allocation, "resolve_artifact_location", fake)
    _stat_double(monkeypatch, ancestor, result=os.stat_result((stat.S_IFREG | 0o644, 0, 0, 1, 0, 0, 7, 0, 0, 0)))
    _fail(request, Code.PROTECTED_OVERLAP)
    assert not (tree / "outputs" / TOKEN).exists()


def test_sibling_names_sharing_a_string_prefix_do_not_overlap(tree):
    (tree / "outputs" / f"{TOKEN}x.wav").write_bytes(b"protected")
    (tree / "outputs" / f"{TOKEN}-other").mkdir()
    (tree / "outputs" / f"{TOKEN}-other" / "kept.wav").write_bytes(b"protected")
    extra = tuple(
        resolve_artifact_location(_artifact(f"sib{i}", location), root=str(tree / "outputs"))
        for i, location in enumerate([f"{TOKEN}x.wav", f"{TOKEN}-other/kept.wav"])
    )
    result = allocate_workspace(_real_request(tree, protected=_inventory(tree) + extra))
    assert result.directory == _expected_directory(tree)


def test_overlap_uses_native_component_comparison(tree):
    # Windows compares path components case-insensitively; POSIX names differing in case are distinct.
    (tree / "outputs" / TOKEN.upper()).mkdir()
    (tree / "outputs" / TOKEN.upper() / "kept.wav").write_bytes(b"protected")
    record = resolve_artifact_location(_artifact("kept", f"{TOKEN.upper()}/kept.wav"), root=str(tree / "outputs"))
    request = _real_request(tree, protected=_inventory(tree) + (record,))
    if WINDOWS:
        _fail(request, Code.PROTECTED_OVERLAP)
    else:
        assert allocate_workspace(request).directory == _expected_directory(tree)


def test_protected_location_failures_precede_overlap(tree):
    (tree / "outputs" / TOKEN).write_bytes(b"protected")
    overlap = resolve_artifact_location(_artifact("out", TOKEN), root=str(tree / "outputs"))
    retained = resolve_artifact_location(RETAINED, root=str(tree / "sources"))
    (tree / "sources" / "retained.wav").unlink()
    _fail(_real_request(tree, protected=(overlap,) + _inventory(tree) + (retained,)), Code.PROTECTED_LOCATION)


# --- collisions -----------------------------------------------------------------------------------


def _make_collision(tree, kind):
    child, outside = tree / "outputs" / TOKEN, tree / "outside"
    outside.mkdir()
    (outside / "inside.txt").write_bytes(b"target")
    if kind == "file":
        child.write_bytes(b"existing")
    elif kind == "empty-dir":
        child.mkdir()
    elif kind == "nonempty-dir":
        child.mkdir()
        (child / "old.txt").write_bytes(b"old")
    elif kind == "file-symlink":
        _symlink(outside / "inside.txt", child, directory=False)
    elif kind == "dir-symlink":
        _symlink(outside, child, directory=True)
    elif kind == "dangling-file-symlink":
        _symlink(outside / "missing.txt", child, directory=False)
    elif kind == "dangling-dir-symlink":
        _symlink(outside / "missing", child, directory=True)
    elif kind == "junction":
        _junction(outside, child)
    elif kind == "dangling-junction":
        (tree / "gone").mkdir()
        _junction(tree / "gone", child)
        _REAL.rmdir(tree / "gone")


_JUNCTIONS_ONLY = pytest.mark.skipif(not WINDOWS, reason="junctions exist only on Windows")
COLLISION_KINDS = [
    "file",
    "empty-dir",
    "nonempty-dir",
    "file-symlink",
    "dir-symlink",
    "dangling-file-symlink",
    "dangling-dir-symlink",
    pytest.param("junction", marks=_JUNCTIONS_ONLY),
    pytest.param("dangling-junction", marks=_JUNCTIONS_ONLY),
]


@pytest.mark.parametrize("kind", COLLISION_KINDS)
def test_every_existing_child_form_is_a_collision_and_stays_untouched(tree, monkeypatch, kind):
    _make_collision(tree, kind)
    request = _real_request(tree)
    before = _snapshot(tree)
    forbidden = _forbid_mutation_and_content(monkeypatch)
    mkdir = _Mkdir(monkeypatch)
    error = _fail(request, Code.COLLISION)
    monkeypatch.undo()
    assert isinstance(error.__cause__, FileExistsError) and error.created_directory is None
    # The collision comes from the exclusive mkdir itself, not from an existence pre-check.
    assert mkdir.path_calls == [(_expected_directory(tree), False, False)] and len(mkdir.os_calls) == 1
    assert forbidden == [] and _snapshot(tree) == before


@pytest.mark.parametrize(
    "exc, code",
    [
        (FileExistsError(errno.EEXIST, "exists"), Code.COLLISION),
        (PermissionError(errno.EACCES, "denied"), Code.NOT_WRITABLE),
        (FileNotFoundError(errno.ENOENT, "root gone"), Code.WORKSPACE_CHANGED),
        (NotADirectoryError(errno.ENOTDIR, "root replaced"), Code.WORKSPACE_CHANGED),
    ],
    ids=_id,
)
def test_mkdir_errors_map_narrowly_without_a_created_directory(tree, monkeypatch, exc, code):
    mkdir = _Mkdir(monkeypatch, fail=exc)
    error = _fail(_real_request(tree), code)
    assert error.__cause__ is exc and error.created_directory is None and len(mkdir.path_calls) == 1


@pytest.mark.parametrize("exc", UNEXPECTED + [OSError(errno.ENOSPC, "full")], ids=_id)
def test_unexpected_mkdir_errors_propagate_unchanged(tree, monkeypatch, exc):
    _Mkdir(monkeypatch, fail=exc)
    with pytest.raises(type(exc)) as info:
        allocate_workspace(_real_request(tree))
    assert info.value is exc


@pytest.mark.parametrize("replacement", ["removed", "file"])
def test_real_root_changed_before_mkdir_is_a_workspace_change(tree, monkeypatch, replacement):
    (tree / "empty-root").mkdir()
    request = _real_request(tree, root_path=tree / "empty-root")
    real_mkdir = Path.mkdir

    def change_root_then_mkdir(path, mode=0o777, parents=False, exist_ok=False):
        _REAL.rmdir(tree / "empty-root")
        if replacement == "file":
            with _REAL.open(tree / "empty-root", "wb") as handle:
                handle.write(b"x")
        real_mkdir(path, mode, parents, exist_ok)

    monkeypatch.setattr(Path, "mkdir", change_root_then_mkdir)
    error = _fail(request, Code.WORKSPACE_CHANGED)
    assert error.created_directory is None and not (tree / "empty-root" / TOKEN).exists()


# --- postchecks after a successful mkdir --------------------------------------------------------


def _created(mkdir):
    return lambda: mkdir.created


POSTCHECK_EXPECTED = [
    FileNotFoundError(errno.ENOENT, "gone"),
    NotADirectoryError(errno.ENOTDIR, "replaced"),
    PermissionError(errno.EACCES, "denied"),
    OSError(errno.ELOOP, "loop"),
    RuntimeError("Symlink loop from 'x'"),
]


@pytest.mark.parametrize("exc", POSTCHECK_EXPECTED, ids=_id)
@pytest.mark.parametrize("which", ["root", "child"])
def test_postcheck_resolution_failures_are_workspace_changes_that_retain_the_directory(tree, monkeypatch, which, exc):
    request = _real_request(tree)
    target = request.root.path if which == "root" else _expected_directory(tree)
    forbidden = _forbid_mutation_and_content(monkeypatch)
    mkdir = _Mkdir(monkeypatch)
    calls = _resolve_double(monkeypatch, target, when=_created(mkdir), exc=exc)
    error = _fail(request, Code.WORKSPACE_CHANGED)
    monkeypatch.undo()
    assert calls == [True] and error.__cause__ is exc and forbidden == []
    assert error.created_directory == _expected_directory(tree)
    assert Path(error.created_directory).is_dir()


@pytest.mark.parametrize("which", ["root", "child"])
def test_postcheck_redirected_root_or_child_is_a_workspace_change(tree, monkeypatch, which):
    request = _real_request(tree)
    target = request.root.path if which == "root" else _expected_directory(tree)
    mkdir = _Mkdir(monkeypatch)
    _resolve_double(monkeypatch, target, when=_created(mkdir), result=_canonical(tree / "foreign"))
    error = _fail(request, Code.WORKSPACE_CHANGED)
    assert error.created_directory == _expected_directory(tree) and Path(error.created_directory).is_dir()


@pytest.mark.parametrize("exc", UNEXPECTED + [OSError(errno.ENAMETOOLONG, "long")], ids=_id)
@pytest.mark.parametrize("which", ["root", "child"])
def test_unexpected_postcheck_resolution_errors_propagate_and_retain_the_directory(tree, monkeypatch, which, exc):
    request = _real_request(tree)
    target = request.root.path if which == "root" else _expected_directory(tree)
    mkdir = _Mkdir(monkeypatch)
    _resolve_double(monkeypatch, target, when=_created(mkdir), exc=exc)
    with pytest.raises(type(exc)) as info:
        allocate_workspace(request)
    assert info.value is exc
    monkeypatch.undo()
    assert Path(_expected_directory(tree)).is_dir()


def _lstat_double(monkeypatch, target: str, *, when, exc=None, result=None):
    real = os.lstat

    def fake(path, *args, **kwargs):
        if os.fspath(path) == target and when():
            if exc is not None:
                raise exc
            return result
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", fake)


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_file_attributes=0),
        SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_file_attributes=0),
        SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT),
    ],
    ids=["symlink", "regular", "reparse-point"],
)
def test_postcheck_child_must_lstat_as_a_real_directory(tree, monkeypatch, result):
    mkdir = _Mkdir(monkeypatch)
    _lstat_double(monkeypatch, _expected_directory(tree), when=_created(mkdir), result=result)
    error = _fail(_real_request(tree), Code.WORKSPACE_CHANGED)
    assert error.created_directory == _expected_directory(tree)


POSTCHECK_INSPECTION = [
    FileNotFoundError(errno.ENOENT, "gone"),
    NotADirectoryError(errno.ENOTDIR, "replaced"),
    PermissionError(errno.EACCES, "denied"),
]


@pytest.mark.parametrize("exc", POSTCHECK_INSPECTION, ids=_id)
def test_postcheck_lstat_failures_are_workspace_changes(tree, monkeypatch, exc):
    mkdir = _Mkdir(monkeypatch)
    _lstat_double(monkeypatch, _expected_directory(tree), when=_created(mkdir), exc=exc)
    error = _fail(_real_request(tree), Code.WORKSPACE_CHANGED)
    assert error.__cause__ is exc and error.created_directory == _expected_directory(tree)


def _scandir_double(monkeypatch, target: str, exc):
    real = os.scandir

    def fake(path="."):
        if os.fspath(path) == target:
            raise exc
        return real(path)

    monkeypatch.setattr(os, "scandir", fake)


@pytest.mark.parametrize("exc", POSTCHECK_INSPECTION, ids=_id)
def test_postcheck_listing_failures_are_workspace_changes(tree, monkeypatch, exc):
    _scandir_double(monkeypatch, _expected_directory(tree), exc)
    error = _fail(_real_request(tree), Code.WORKSPACE_CHANGED)
    assert error.__cause__ is exc and error.created_directory == _expected_directory(tree)


@pytest.mark.parametrize("exc", [OSError(errno.EIO, "I/O"), MemoryError()], ids=_id)
def test_unexpected_listing_errors_propagate(tree, monkeypatch, exc):
    _scandir_double(monkeypatch, _expected_directory(tree), exc)
    with pytest.raises(type(exc)) as info:
        allocate_workspace(_real_request(tree))
    assert info.value is exc


@pytest.mark.parametrize(
    "exc", [OSError(errno.EIO, "I/O"), MemoryError(), WorkspaceAllocationError(Code.COLLISION, "own")], ids=_id
)
def test_unexpected_lstat_errors_and_own_errors_propagate_unchanged(tree, monkeypatch, exc):
    mkdir = _Mkdir(monkeypatch)
    _lstat_double(monkeypatch, _expected_directory(tree), when=_created(mkdir), exc=exc)
    with pytest.raises(type(exc)) as info:
        allocate_workspace(_real_request(tree))
    assert info.value is exc


def test_postcheck_new_contents_are_a_workspace_change_and_are_kept(tree, monkeypatch):
    def add_file(path):
        with _REAL.open(path / "intruder.txt", "wb") as handle:
            handle.write(b"x")

    _Mkdir(monkeypatch, after=add_file)
    error = _fail(_real_request(tree), Code.WORKSPACE_CHANGED)
    assert os.listdir(error.created_directory) == ["intruder.txt"]


def _replace_child_with(kind, tree):
    def after(path):
        _REAL.rmdir(path)
        if kind == "file":
            with _REAL.open(path, "wb") as handle:
                handle.write(b"x")
        elif kind == "symlink":
            _symlink(tree / "foreign", path, directory=True)
        elif kind == "junction":
            _junction(tree / "foreign", path)

    return after


@pytest.mark.parametrize("kind", ["missing", "file", "symlink", pytest.param("junction", marks=_JUNCTIONS_ONLY)])
def test_real_child_replacement_after_mkdir_is_a_workspace_change_and_left_untouched(tree, monkeypatch, kind):
    _Mkdir(monkeypatch, after=_replace_child_with(kind, tree))
    error = _fail(_real_request(tree), Code.WORKSPACE_CHANGED)
    monkeypatch.undo()
    assert error.created_directory == _expected_directory(tree)
    child = tree / "outputs" / TOKEN
    if kind in ("symlink", "junction"):
        assert Path(os.readlink(child)).name == "foreign"
        assert os.listdir(tree / "foreign") == ["foreign.wav"]
    elif kind == "file":
        assert child.read_bytes() == b"x"


def test_real_root_replaced_by_a_symlink_after_mkdir_is_a_workspace_change(tree, monkeypatch):
    def swap_root(path):
        _REAL.rename(tree / "outputs", tree / "moved")
        _symlink(tree / "foreign", tree / "outputs", directory=True)

    _Mkdir(monkeypatch, after=swap_root)
    request = _real_request(tree)
    expected = _expected_directory(tree)
    error = _fail(request, Code.WORKSPACE_CHANGED)
    assert error.created_directory == expected
    assert (tree / "moved" / TOKEN).is_dir() and os.listdir(tree / "foreign") == ["foreign.wav"]


# --- retention and retries ------------------------------------------------------------------------


def test_post_mkdir_failure_retains_the_directory_and_a_retry_collides(tree, monkeypatch):
    request = _real_request(tree)
    forbidden = _forbid_mutation_and_content(monkeypatch)
    mkdir = _Mkdir(monkeypatch)
    denied = PermissionError(errno.EACCES, "denied")
    _lstat_double(monkeypatch, _expected_directory(tree), when=_created(mkdir), exc=denied)
    error = _fail(request, Code.WORKSPACE_CHANGED)
    monkeypatch.undo()
    assert forbidden == [] and len(mkdir.os_calls) == 1
    assert error.created_directory == _expected_directory(tree)
    assert Path(error.created_directory).is_dir() and os.listdir(error.created_directory) == []
    retry = _fail(request, Code.COLLISION)  # no automatic suffix, retry or reuse
    assert retry.created_directory is None
    assert sorted(os.listdir(tree / "outputs")) == ["earlier.txt", TOKEN]


# --- concurrency ----------------------------------------------------------------------------------


def _race(requests):
    barrier = threading.Barrier(len(requests), timeout=30)
    outcomes = [None] * len(requests)

    def run(index):
        barrier.wait()
        try:
            outcomes[index] = allocate_workspace(requests[index])
        except BaseException as exc:  # recorded and asserted below, never swallowed
            outcomes[index] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(requests))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "allocation thread did not finish"
    return outcomes


def test_concurrent_allocations_of_one_assignment_yield_one_directory_and_one_collision(tree):
    # Repeated rounds with fresh tokens; this tests exclusive name creation, not an execution lock.
    tokens = [f"race-{i}" for i in range(10)]
    for token in tokens:
        request = _real_request(tree, token=token)
        outcomes = _race([request, request])
        successes = [o for o in outcomes if isinstance(o, AllocatedWorkspace)]
        failures = [o for o in outcomes if isinstance(o, WorkspaceAllocationError)]
        assert len(successes) == 1 and len(failures) == 1, outcomes
        assert failures[0].code is Code.COLLISION and failures[0].created_directory is None
        assert successes[0].request is request and os.listdir(successes[0].directory) == []
    assert sorted(os.listdir(tree / "outputs")) == sorted(["earlier.txt", *tokens])


def test_concurrent_allocations_of_distinct_tokens_both_succeed(tree):
    outcomes = _race([_real_request(tree, token="left"), _real_request(tree, token="right")])
    assert all(isinstance(o, AllocatedWorkspace) for o in outcomes), outcomes
    assert sorted(os.listdir(tree / "outputs")) == ["earlier.txt", "left", "right"]
