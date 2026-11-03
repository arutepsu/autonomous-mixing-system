"""Tests for read-only artifact-location resolution against an explicit filesystem root.

Fixtures are directories and small placeholder files created by the tests in ``tmp_path``; no project
media or audio content is used, and resolution never reads file content. Windows path forms are
tested deterministically through the pure lexical helper; native filesystem tests run on the host
platform and name their platform scope. Filesystem errors the host cannot produce reliably are
simulated with controlled doubles of ``Path.resolve`` or ``os.stat``; these doubles exercise the
error mapping only and do not validate real junction, alias or permission behaviour.
"""

import builtins
import dataclasses
import errno
import io
import os
import stat
from pathlib import Path

import pytest

import autonomous_mixing.adapters.rendering.locations as locations  # moved in ADR-027 step M3; tests patch or inspect its internals
from autonomous_mixing.domain.project import ArtifactKind, AudioArtifact
from autonomous_mixing.domain.shared import ArtifactId, ProjectId, Revision, TrackId, VersionRef
from autonomous_mixing.infrastructure.filesystem import (
    LocationResolutionError,
    LocationResolutionFailureCode,
    ResolvedArtifactLocation,
    resolve_artifact_location,
)

Code = LocationResolutionFailureCode
WINDOWS = os.name == "nt"


def _artifact(location) -> AudioArtifact:
    return AudioArtifact(
        ArtifactId("a1"),
        ProjectId("p1"),
        Revision("r1"),
        (TrackId("t1"),),
        ArtifactKind.STEM,
        str(location),
        0.0,
        48000,
        1,
        4,
        "0" * 64,
        VersionRef("render", "1"),
    )


def _failure(artifact, root, code) -> LocationResolutionError:
    with pytest.raises(LocationResolutionError) as info:
        resolve_artifact_location(artifact, root=root)
    assert info.value.code is code, info.value
    assert isinstance(info.value, ValueError)
    assert str(info.value).startswith(f"{code.value}: ")
    return info.value


class _Forbidden(AssertionError):
    pass


def _forbid_filesystem(monkeypatch) -> list:
    """Make every filesystem entry point the resolver could use fail loudly and record the attempt."""
    touched = []

    def deny(name):
        def call(*args, **kwargs):
            touched.append(name)
            raise _Forbidden(f"filesystem access via {name}")

        return call

    monkeypatch.setattr(Path, "resolve", deny("Path.resolve"))
    monkeypatch.setattr(os, "stat", deny("os.stat"))
    monkeypatch.setattr(os, "lstat", deny("os.lstat"))
    monkeypatch.setattr(os, "open", deny("os.open"))
    monkeypatch.setattr(builtins, "open", deny("open"))
    monkeypatch.setattr(io, "open", deny("io.open"))
    return touched


@pytest.fixture
def tree(tmp_path):
    """``root/outputs/stem.wav`` plus a sibling ``root-other`` that shares the root's string prefix."""
    root = tmp_path / "root"
    (root / "outputs").mkdir(parents=True)
    (root / "outputs" / "stem.wav").write_bytes(b"placeholder")
    other = tmp_path / "root-other"
    other.mkdir()
    (other / "stem.wav").write_bytes(b"placeholder")
    return root


# --- public interface ----------------------------------------------------------------------


def test_public_interface_exports_exactly_the_four_definitions():
    import autonomous_mixing.infrastructure.filesystem as filesystem

    assert sorted(filesystem.__all__) == sorted(
        [
            "ResolvedArtifactLocation",
            "LocationResolutionFailureCode",
            "LocationResolutionError",
            "resolve_artifact_location",
        ]
    )
    assert filesystem.ResolvedArtifactLocation is locations.ResolvedArtifactLocation
    assert filesystem.LocationResolutionFailureCode is locations.LocationResolutionFailureCode
    assert filesystem.LocationResolutionError is locations.LocationResolutionError
    assert filesystem.resolve_artifact_location is locations.resolve_artifact_location


def test_failure_codes_are_exactly_the_specified_members_with_lowercase_values():
    assert [(code.name, code.value) for code in Code] == [
        ("UNSUPPORTED_PATH", "unsupported_path"),
        ("INVALID_ROOT", "invalid_root"),
        ("NOT_FOUND", "not_found"),
        ("NOT_READABLE", "not_readable"),
        ("OUTSIDE_ROOT", "outside_root"),
        ("RESOLUTION_LOOP", "resolution_loop"),
        ("STALE_RESOLUTION", "stale_resolution"),
    ]


@pytest.mark.parametrize("code", list(Code), ids=lambda c: c.name)
def test_resolution_error_carries_its_code_and_prefixes_the_message(code):
    error = LocationResolutionError(code, "detail")
    assert isinstance(error, ValueError)
    assert error.code is code
    assert str(error) == f"{code.value}: detail"


# --- the resolution record -----------------------------------------------------------------


def test_record_is_frozen_and_keeps_its_fields_without_filesystem_access(monkeypatch):
    artifact = _artifact("outputs/stem.wav")
    touched = _forbid_filesystem(monkeypatch)
    record = ResolvedArtifactLocation(artifact, "configured", "resolved", "absolute")
    assert touched == []
    assert record.artifact is artifact
    assert (record.configured_root, record.resolved_root, record.absolute_path) == (
        "configured",
        "resolved",
        "absolute",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.absolute_path = "elsewhere"  # type: ignore[misc]
    assert [f.name for f in dataclasses.fields(ResolvedArtifactLocation)] == [
        "artifact",
        "configured_root",
        "resolved_root",
        "absolute_path",
    ]


def test_record_does_not_certify_existence_or_canonicality(monkeypatch):
    # Construction validates types only; a stale or fictional record can be built without any I/O.
    touched = _forbid_filesystem(monkeypatch)
    ResolvedArtifactLocation(_artifact("x.wav"), "/no/such/root", "/no/such/root", "/no/such/root/x.wav")
    assert touched == []


@pytest.mark.parametrize(
    "fields",
    [
        {"artifact": "outputs/stem.wav"},
        {"artifact": None},
        {"configured_root": Path("/root")},
        {"configured_root": ""},
        {"configured_root": "   "},
        {"resolved_root": None},
        {"resolved_root": "\t"},
        {"absolute_path": b"/root/x.wav"},
        {"absolute_path": ""},
    ],
    ids=repr,
)
def test_record_rejects_wrong_types_and_blank_strings_without_filesystem_access(monkeypatch, fields):
    values = {"artifact": _artifact("x.wav"), "configured_root": "c", "resolved_root": "r", "absolute_path": "a"}
    touched = _forbid_filesystem(monkeypatch)
    with pytest.raises(ValueError) as info:
        ResolvedArtifactLocation(**{**values, **fields})
    assert not isinstance(info.value, LocationResolutionError)
    assert touched == []


# --- argument checks -------------------------------------------------------------------------


@pytest.mark.parametrize("artifact", ["outputs/stem.wav", None, 3], ids=repr)
def test_artifact_must_be_an_audio_artifact_before_filesystem_access(monkeypatch, tmp_path, artifact):
    touched = _forbid_filesystem(monkeypatch)
    with pytest.raises(ValueError) as info:
        resolve_artifact_location(artifact, root=str(tmp_path))
    assert not isinstance(info.value, LocationResolutionError)
    assert touched == []


@pytest.mark.parametrize("root", [None, b"/root", 3, "", "   ", "\n"], ids=repr)
def test_root_must_be_a_non_blank_string_before_filesystem_access(monkeypatch, root):
    touched = _forbid_filesystem(monkeypatch)
    with pytest.raises(ValueError) as info:
        resolve_artifact_location(_artifact("outputs/stem.wav"), root=root)
    assert not isinstance(info.value, LocationResolutionError)
    assert touched == []


def test_path_object_root_is_rejected_as_an_argument_error(tmp_path):
    with pytest.raises(ValueError) as info:
        resolve_artifact_location(_artifact("outputs/stem.wav"), root=tmp_path)
    assert not isinstance(info.value, LocationResolutionError)


def test_root_is_keyword_only(tmp_path):
    with pytest.raises(TypeError):
        resolve_artifact_location(_artifact("outputs/stem.wav"), str(tmp_path))  # type: ignore[misc]


# --- lexical policy: deterministic Windows and POSIX forms via the pure helper ----------------

# (text, windows flavour, require_absolute) -> rejected
LEXICAL_REJECTED = [
    # NUL characters on either flavour
    ("C:\\root\\a\x00.wav", True, True),
    ("outputs/a\x00.wav", True, False),
    ("/root/a\x00.wav", False, True),
    ("outputs/a\x00.wav", False, False),
    # URI schemes
    ("file:///C:/root", True, True),
    ("file:C:/root/a.wav", True, False),
    ("FILE:///tmp/root", False, True),
    ("https://example.org/a.wav", False, False),
    ("s3://bucket/a.wav", True, False),
    # UNC / network / device namespaces, on both flavours
    ("\\\\server\\share\\root", True, True),
    ("//server/share/root", True, True),
    ("\\\\?\\C:\\root", True, True),
    ("\\\\.\\C:\\root", True, True),
    ("/\\server/share/a.wav", True, False),
    ("\\\\server\\share\\a.wav", False, False),
    ("//server/share/root", False, True),
    # parent-directory components
    ("..\\a.wav", True, False),
    ("outputs/../a.wav", True, False),
    ("outputs\\..\\..\\a.wav", True, False),
    ("C:\\root\\..\\other", True, True),
    ("..", True, False),
    ("../a.wav", False, False),
    ("outputs/../a.wav", False, False),
    ("/root/../other", False, True),
    ("outputs/..", False, False),
    # Windows drive-relative and root-relative forms
    ("C:a.wav", True, False),
    ("C:outputs\\a.wav", True, False),
    ("C:", True, True),
    ("\\a.wav", True, False),
    ("/a.wav", True, False),
    ("\\root", True, True),
    ("/root", True, True),
    # Windows colons outside the drive prefix, including alternate data streams
    ("a.wav:stream", True, False),
    ("outputs/a.wav:stream:$DATA", True, False),
    ("C:\\root\\a.wav:stream", True, False),
    ("C:\\root:stream", True, True),
    ("x:y\\z:w", True, False),
    # Windows drive and UNC syntax on POSIX is not an ordinary relative name
    ("C:\\root", False, True),
    ("C:/root/a.wav", False, False),
    ("C:a.wav", False, False),
    ("c:outputs/a.wav", False, False),
    # relative roots
    ("root", True, True),
    ("outputs\\root", True, True),
    (".", False, True),
    ("root/sub", False, True),
]

LEXICAL_ACCEPTED = [
    ("C:\\root", True, True),
    ("C:/root/sub", True, True),
    ("d:\\", True, True),
    ("C:\\root\\a.wav", True, False),
    ("outputs\\a.wav", True, False),
    ("outputs/./a.wav", True, False),
    ("./a.wav", True, False),
    ("C:\\Root With Spaces\\Ünïcødé\\a b.wav", True, False),
    ("...wav", True, False),
    ("a..b.wav", True, False),
    ("/root", False, True),
    ("/", False, True),
    ("/root/a.wav", False, False),
    ("outputs/a.wav", False, False),
    ("./outputs/./a.wav", False, False),
    ("dir with spaces/ünïcødé.wav", False, False),
    # POSIX names are not held to Windows conventions: backslashes and dots are ordinary characters
    ("outputs\\a.wav", False, False),
    ("..\\a.wav", False, False),
    ("a..b.wav", False, False),
]


@pytest.mark.parametrize("text, windows, require_absolute", LEXICAL_REJECTED, ids=repr)
def test_lexical_helper_rejects_unsupported_forms(text, windows, require_absolute):
    assert locations._lexical_problem(text, windows=windows, require_absolute=require_absolute) is not None


@pytest.mark.parametrize("text, windows, require_absolute", LEXICAL_ACCEPTED, ids=repr)
def test_lexical_helper_accepts_supported_forms(text, windows, require_absolute):
    assert locations._lexical_problem(text, windows=windows, require_absolute=require_absolute) is None


def test_lexical_helper_flavour_follows_the_running_platform(monkeypatch):
    calls = []
    real = locations._lexical_problem

    def spy(text, *, windows, require_absolute):
        calls.append(windows)
        return real(text, windows=windows, require_absolute=require_absolute)

    monkeypatch.setattr(locations, "_lexical_problem", spy)
    _forbid_filesystem(monkeypatch)
    with pytest.raises(LocationResolutionError):
        resolve_artifact_location(_artifact("../stem.wav"), root="C:\\root" if WINDOWS else "/root")
    assert calls and set(calls) == {WINDOWS}


# --- lexical policy through the public resolver (native platform) -----------------------------

NATIVE_ROOT_REJECTED = [
    "root",
    "./root",
    "file:///root",
    "\\\\server\\share",
    "//server/share",
    "/root/../x",
    "r\x00",
] + (["\\root", "/root", "C:root", "C:\\root:stream"] if WINDOWS else ["C:\\root", "C:/root"])
NATIVE_LOCATION_REJECTED = [
    "../stem.wav",
    "outputs/../../stem.wav",
    "file:///stem.wav",
    "https://example.org/stem.wav",
    "\\\\server\\share\\stem.wav",
    "//server/share/stem.wav",
    "outputs/st\x00em.wav",
] + (
    ["\\stem.wav", "/stem.wav", "C:stem.wav", "stem.wav:stream", "outputs\\..\\..\\stem.wav"]
    if WINDOWS
    else ["C:\\stem.wav", "C:/stem.wav", "C:stem.wav"]
)


@pytest.mark.parametrize("root", NATIVE_ROOT_REJECTED, ids=repr)
def test_native_unsupported_roots_are_rejected_before_filesystem_access(monkeypatch, root):
    # Native platform: forms listed for Windows on nt, POSIX forms elsewhere.
    touched = _forbid_filesystem(monkeypatch)
    _failure(_artifact("outputs/stem.wav"), root, Code.UNSUPPORTED_PATH)
    assert touched == []


@pytest.mark.parametrize("location", NATIVE_LOCATION_REJECTED, ids=repr)
def test_native_unsupported_locations_are_rejected_before_filesystem_access(monkeypatch, tree, location):
    touched = _forbid_filesystem(monkeypatch)
    _failure(_artifact(location), str(tree), Code.UNSUPPORTED_PATH)
    assert touched == []


def test_root_lexical_failure_precedes_location_lexical_failure(monkeypatch):
    touched = _forbid_filesystem(monkeypatch)
    error = _failure(_artifact("../stem.wav"), "relative-root", Code.UNSUPPORTED_PATH)
    assert "root" in str(error)
    assert "relative-root" in str(error)
    assert touched == []


def test_location_lexical_failure_precedes_root_existence(monkeypatch, tmp_path):
    missing_root = str(tmp_path / "missing-root")
    touched = _forbid_filesystem(monkeypatch)
    _failure(_artifact("../stem.wav"), missing_root, Code.UNSUPPORTED_PATH)
    assert touched == []


# --- strict resolution and containment (native filesystem) ------------------------------------


def _canonical(path: Path) -> str:
    return str(path.resolve(strict=True))


def _assert_same_artifact(record, artifact, location):
    assert record.artifact is artifact
    assert artifact.location == location
    assert dataclasses.astuple(record.artifact) == dataclasses.astuple(_artifact(location))


def test_relative_location_resolves_inside_the_root_and_keeps_the_original_artifact(tree):
    artifact = _artifact("outputs/stem.wav")
    root = str(tree)
    record = resolve_artifact_location(artifact, root=root)
    assert type(record) is ResolvedArtifactLocation
    _assert_same_artifact(record, artifact, "outputs/stem.wav")
    assert record.configured_root == root
    assert record.resolved_root == _canonical(tree)
    assert record.absolute_path == _canonical(tree / "outputs" / "stem.wav")
    assert Path(record.absolute_path).is_absolute() and Path(record.resolved_root).is_absolute()


def test_absolute_location_inside_the_root_is_used_directly(tree):
    location = str(tree / "outputs" / "stem.wav")
    artifact = _artifact(location)
    record = resolve_artifact_location(artifact, root=str(tree))
    _assert_same_artifact(record, artifact, location)
    assert record.absolute_path == _canonical(tree / "outputs" / "stem.wav")


@pytest.mark.parametrize(
    "location",
    ["./outputs/stem.wav", "outputs/./stem.wav", "./outputs/././stem.wav"]
    + (["outputs\\stem.wav", ".\\outputs\\stem.wav"] if WINDOWS else []),
    ids=repr,
)
def test_permitted_dot_components_and_native_separators_resolve(tree, location):
    record = resolve_artifact_location(_artifact(location), root=str(tree))
    assert record.absolute_path == _canonical(tree / "outputs" / "stem.wav")
    assert record.artifact.location == location


def test_spaces_and_unicode_in_root_and_location_resolve(tmp_path):
    root = tmp_path / "Root With Spaces ünï"
    (root / "dir ä ö").mkdir(parents=True)
    (root / "dir ä ö" / "stem ß €.wav").write_bytes(b"placeholder")
    record = resolve_artifact_location(_artifact("dir ä ö/stem ß €.wav"), root=str(root))
    assert record.absolute_path == _canonical(root / "dir ä ö" / "stem ß €.wav")


def test_configured_root_is_retained_verbatim(tree):
    root = str(tree) + os.sep + "." + os.sep
    record = resolve_artifact_location(_artifact("outputs/stem.wav"), root=root)
    assert record.configured_root == root
    assert record.resolved_root == _canonical(tree)


def test_nested_directory_resolves_because_regular_file_checks_belong_to_the_verifier(tree):
    record = resolve_artifact_location(_artifact("outputs"), root=str(tree))
    assert record.absolute_path == _canonical(tree / "outputs")


def test_sibling_root_sharing_a_string_prefix_is_outside(tree):
    sibling = tree.parent / "root-other" / "stem.wav"
    assert str(sibling).startswith(str(tree))  # the string prefix really matches
    _failure(_artifact(str(sibling)), str(tree), Code.OUTSIDE_ROOT)


@pytest.mark.parametrize(
    "make", [lambda root: str(root), lambda root: ".", lambda root: "./"], ids=["absolute", "dot", "dot-slash"]
)
def test_root_itself_is_not_an_artifact_descendant(tree, make):
    _failure(_artifact(make(tree)), str(tree), Code.OUTSIDE_ROOT)


def test_absolute_location_outside_the_root_is_rejected(tree, tmp_path):
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"placeholder")
    _failure(_artifact(str(outside)), str(tree), Code.OUTSIDE_ROOT)


def test_missing_outside_looking_candidate_is_not_found_before_containment(tree):
    missing = tree.parent / "root-other" / "missing.wav"
    _failure(_artifact(str(missing)), str(tree), Code.NOT_FOUND)


def test_missing_root_is_invalid(tmp_path):
    _failure(_artifact("outputs/stem.wav"), str(tmp_path / "missing"), Code.INVALID_ROOT)


def test_missing_intermediate_root_component_is_invalid(tmp_path):
    _failure(_artifact("outputs/stem.wav"), str(tmp_path / "missing" / "root"), Code.INVALID_ROOT)


def test_root_that_is_a_file_is_invalid(tmp_path):
    file_root = tmp_path / "file-root"
    file_root.write_bytes(b"placeholder")
    _failure(_artifact("stem.wav"), str(file_root), Code.INVALID_ROOT)


def test_root_below_a_file_is_invalid(tmp_path):
    file_root = tmp_path / "file-root"
    file_root.write_bytes(b"placeholder")
    _failure(_artifact("stem.wav"), str(file_root / "sub"), Code.INVALID_ROOT)


@pytest.mark.parametrize(
    "location", ["outputs/missing.wav", "missing/stem.wav", "outputs/stem.wav/inner.wav"], ids=repr
)
def test_missing_candidate_or_intermediate_component_is_not_found(tree, location):
    _failure(_artifact(location), str(tree), Code.NOT_FOUND)


def test_resolution_does_not_depend_on_the_current_directory(tree, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "outputs").mkdir(parents=True)
    (elsewhere / "outputs" / "stem.wav").write_bytes(b"decoy")
    artifact = _artifact("outputs/stem.wav")
    results = []
    for cwd in (elsewhere, tree / "outputs"):
        monkeypatch.chdir(cwd)
        results.append(resolve_artifact_location(artifact, root=str(tree)))
    assert results[0] == results[1]
    assert results[0].absolute_path == _canonical(tree / "outputs" / "stem.wav")
    monkeypatch.chdir(elsewhere)
    _failure(_artifact("outputs/stem.wav"), "outputs", Code.UNSUPPORTED_PATH)  # no cwd fallback for the root


def _snapshot(base: Path) -> dict:
    return {
        str(p): (p.is_dir(), None if p.is_dir() else p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(base.rglob("*"))
    }


MUTATORS = [
    (os, "remove"),
    (os, "unlink"),
    (os, "rename"),
    (os, "replace"),
    (os, "mkdir"),
    (os, "makedirs"),
    (os, "rmdir"),
    (os, "symlink"),
    (os, "truncate"),
    (os, "utime"),
    (os, "chmod"),
    (Path, "mkdir"),
    (Path, "touch"),
    (Path, "unlink"),
    (Path, "rename"),
    (Path, "replace"),
    (Path, "rmdir"),
    (Path, "write_bytes"),
    (Path, "write_text"),
    (Path, "chmod"),
]


def _forbid_content_and_mutation(monkeypatch) -> list:
    calls = []

    def deny(name):
        def call(*args, **kwargs):
            calls.append(name)
            raise _Forbidden(name)

        return call

    for owner, name in MUTATORS:
        if hasattr(owner, name):
            monkeypatch.setattr(owner, name, deny(f"{owner.__name__}.{name}"))
    monkeypatch.setattr(builtins, "open", deny("open"))
    monkeypatch.setattr(io, "open", deny("io.open"))
    monkeypatch.setattr(os, "open", deny("os.open"))
    monkeypatch.setattr(Path, "open", deny("Path.open"))
    monkeypatch.setattr(Path, "read_bytes", deny("Path.read_bytes"))
    return calls


@pytest.mark.parametrize(
    "location, root_suffix, code",
    [
        ("outputs/stem.wav", "", None),
        ("outputs", "", None),
        ("outputs/missing.wav", "", Code.NOT_FOUND),
        ("outputs/stem.wav", "missing", Code.INVALID_ROOT),
        (".", "", Code.OUTSIDE_ROOT),
        ("../x.wav", "", Code.UNSUPPORTED_PATH),
    ],
    ids=["success", "directory", "missing", "bad-root", "root-equal", "lexical"],
)
def test_resolution_opens_no_content_and_mutates_nothing(tree, monkeypatch, location, root_suffix, code):
    before = _snapshot(tree.parent)
    calls = _forbid_content_and_mutation(monkeypatch)
    root = str(tree / root_suffix) if root_suffix else str(tree)
    if code is None:
        resolve_artifact_location(_artifact(location), root=root)
    else:
        _failure(_artifact(location), root, code)
    assert calls == []
    monkeypatch.undo()
    assert _snapshot(tree.parent) == before


# --- narrow error mapping through controlled doubles -----------------------------------------
# These doubles replace ``Path.resolve`` or ``os.stat`` to exercise each mapping branch in a known
# context (root = first strict resolution, candidate = second). They are not evidence of real
# junction, alias or permission behaviour.

ROOT, CANDIDATE = 0, 1


def _resolve_double(monkeypatch, fail_on: int, exc: BaseException) -> list:
    real = Path.resolve
    calls = []

    def fake(self, strict=False):
        calls.append((str(self), strict))
        if len(calls) - 1 == fail_on:
            raise exc
        return real(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", fake)
    return calls


def _id(value):
    if isinstance(value, BaseException):
        return f"{type(value).__name__}-{getattr(value, 'errno', '')}"
    if isinstance(value, Code):
        return value.name
    return {ROOT: "root", CANDIDATE: "candidate"}.get(value, repr(value))


@pytest.mark.parametrize(
    "fail_on, exc, code",
    [
        (ROOT, FileNotFoundError(errno.ENOENT, "missing"), Code.INVALID_ROOT),
        (ROOT, NotADirectoryError(errno.ENOTDIR, "not a directory"), Code.INVALID_ROOT),
        (ROOT, PermissionError(errno.EACCES, "denied"), Code.NOT_READABLE),
        (ROOT, OSError(errno.ELOOP, "loop"), Code.RESOLUTION_LOOP),
        (ROOT, RuntimeError("Symlink loop from 'x'"), Code.RESOLUTION_LOOP),
        (CANDIDATE, FileNotFoundError(errno.ENOENT, "missing"), Code.NOT_FOUND),
        (CANDIDATE, NotADirectoryError(errno.ENOTDIR, "not a directory"), Code.NOT_FOUND),
        (CANDIDATE, PermissionError(errno.EACCES, "denied"), Code.NOT_READABLE),
        (CANDIDATE, OSError(errno.ELOOP, "loop"), Code.RESOLUTION_LOOP),
        (CANDIDATE, RuntimeError("Symlink loop from 'x'"), Code.RESOLUTION_LOOP),
    ],
    ids=_id,
)
def test_strict_resolution_errors_map_by_context(tree, monkeypatch, fail_on, exc, code):
    calls = _resolve_double(monkeypatch, fail_on, exc)
    error = _failure(_artifact("outputs/stem.wav"), str(tree), code)
    assert error.__cause__ is exc
    assert len(calls) == fail_on + 1 and all(strict for _, strict in calls)


@pytest.mark.parametrize("fail_on", [ROOT, CANDIDATE], ids=_id)
@pytest.mark.parametrize(
    "exc",
    [
        OSError(errno.EIO, "I/O error"),
        OSError(errno.ENAMETOOLONG, "name too long"),
        MemoryError(),
        RecursionError("maximum recursion depth exceeded"),
        NotImplementedError("unsupported"),
        TypeError("programming error"),
        ValueError("programming error"),
        KeyboardInterrupt(),
    ],
    ids=_id,
)
def test_unexpected_resolution_errors_propagate_unchanged(tree, monkeypatch, fail_on, exc):
    _resolve_double(monkeypatch, fail_on, exc)
    with pytest.raises(type(exc)) as info:
        resolve_artifact_location(_artifact("outputs/stem.wav"), root=str(tree))
    assert info.value is exc
    assert not isinstance(info.value, LocationResolutionError)


def _stat_double(monkeypatch, target: str, exc: BaseException) -> list:
    real = os.stat
    calls = []

    def fake(path, *args, **kwargs):
        if os.fspath(path) == target:
            calls.append(path)
            raise exc
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fake)
    return calls


@pytest.mark.parametrize(
    "exc, code",
    [
        (PermissionError(errno.EACCES, "denied"), Code.NOT_READABLE),
        (FileNotFoundError(errno.ENOENT, "vanished"), Code.INVALID_ROOT),
        (NotADirectoryError(errno.ENOTDIR, "replaced"), Code.INVALID_ROOT),
    ],
    ids=["permission", "vanished", "not-a-directory"],
)
def test_root_directory_inspection_maps_its_errors_without_a_false_predicate(tree, monkeypatch, exc, code):
    calls = _stat_double(monkeypatch, _canonical(tree), exc)
    error = _failure(_artifact("outputs/stem.wav"), str(tree), code)
    assert calls and error.__cause__ is exc


@pytest.mark.parametrize(
    "exc",
    [
        OSError(errno.EIO, "I/O error"),
        OSError(errno.ELOOP, "loop"),
        RuntimeError("Symlink loop from 'x'"),
        MemoryError(),
    ],
    ids=_id,
)
def test_root_directory_inspection_maps_no_loop_or_unexpected_errors(tree, monkeypatch, exc):
    # Loop mapping applies only around Path.resolve; the same errors from the directory check propagate.
    _stat_double(monkeypatch, _canonical(tree), exc)
    with pytest.raises(type(exc)) as info:
        resolve_artifact_location(_artifact("outputs/stem.wav"), root=str(tree))
    assert info.value is exc


def test_root_errors_precede_candidate_errors(tmp_path):
    # Both the root and the candidate are missing: the root failure wins.
    _failure(_artifact("missing/stem.wav"), str(tmp_path / "missing-root"), Code.INVALID_ROOT)


def test_candidate_errors_precede_containment(tree, monkeypatch):
    calls = _resolve_double(monkeypatch, CANDIDATE, PermissionError(errno.EACCES, "denied"))
    _failure(_artifact(str(tree.parent / "root-other" / "stem.wav")), str(tree), Code.NOT_READABLE)
    assert len(calls) == 2


@pytest.mark.skipif(WINDOWS or not hasattr(os, "geteuid") or os.geteuid() == 0, reason="POSIX non-root only")
def test_real_posix_permission_failures_are_not_readable(tree):
    # POSIX: a directory without search permission blocks strict resolution beneath it.
    blocked = tree / "outputs"
    blocked.chmod(0)
    try:
        _failure(_artifact("outputs/stem.wav"), str(tree), Code.NOT_READABLE)
        _failure(_artifact("stem.wav"), str(blocked), Code.NOT_READABLE)
    finally:
        blocked.chmod(stat.S_IRWXU)


# --- real links (symbolic links and, on Windows, junctions) -----------------------------------


def _symlink(target: Path, link: Path, *, directory: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"host cannot create symbolic links: {exc}")


def _junction(target: Path, link: Path) -> None:
    import _winapi

    try:
        _winapi.CreateJunction(str(target), str(link))
    except OSError as exc:
        pytest.skip(f"host cannot create a directory junction: {exc}")


def test_file_symlink_inside_the_root_to_an_inside_target_is_allowed(tree):
    _symlink(tree / "outputs" / "stem.wav", tree / "alias.wav", directory=False)
    record = resolve_artifact_location(_artifact("alias.wav"), root=str(tree))
    assert record.absolute_path == _canonical(tree / "outputs" / "stem.wav")
    assert record.artifact.location == "alias.wav"


def test_directory_symlink_inside_the_root_to_an_inside_target_is_allowed(tree):
    _symlink(tree / "outputs", tree / "linked", directory=True)
    record = resolve_artifact_location(_artifact("linked/stem.wav"), root=str(tree))
    assert record.absolute_path == _canonical(tree / "outputs" / "stem.wav")


def test_symlink_inside_the_root_to_an_outside_target_is_rejected(tree):
    _symlink(tree.parent / "root-other" / "stem.wav", tree / "escape.wav", directory=False)
    _failure(_artifact("escape.wav"), str(tree), Code.OUTSIDE_ROOT)


def test_directory_symlink_inside_the_root_to_an_outside_directory_is_rejected(tree):
    _symlink(tree.parent / "root-other", tree / "escape", directory=True)
    _failure(_artifact("escape/stem.wav"), str(tree), Code.OUTSIDE_ROOT)


def test_symlink_to_the_root_itself_is_rejected_as_root_equal(tree):
    _symlink(tree, tree / "outputs" / "back", directory=True)
    _failure(_artifact("outputs/back"), str(tree), Code.OUTSIDE_ROOT)


def test_absolute_alias_outside_the_root_with_an_inside_target_is_allowed(tree, tmp_path):
    alias = tmp_path / "alias-outside.wav"
    _symlink(tree / "outputs" / "stem.wav", alias, directory=False)
    record = resolve_artifact_location(_artifact(str(alias)), root=str(tree))
    assert record.absolute_path == _canonical(tree / "outputs" / "stem.wav")
    assert record.artifact.location == str(alias)


def test_root_reached_through_a_symlink_records_the_actual_directory(tree, tmp_path):
    alias_root = tmp_path / "root-alias"
    _symlink(tree, alias_root, directory=True)
    record = resolve_artifact_location(_artifact("outputs/stem.wav"), root=str(alias_root))
    assert record.configured_root == str(alias_root)
    assert record.resolved_root == _canonical(tree)
    assert record.absolute_path == _canonical(tree / "outputs" / "stem.wav")


def test_symlink_cycle_below_the_root_is_a_resolution_loop(tree):
    _symlink(tree / "b", tree / "a", directory=True)
    _symlink(tree / "a", tree / "b", directory=True)
    _failure(_artifact("a/stem.wav"), str(tree), Code.RESOLUTION_LOOP)


def test_self_referencing_symlink_is_a_resolution_loop(tree):
    _symlink(tree / "self.wav", tree / "self.wav", directory=False)
    _failure(_artifact("self.wav"), str(tree), Code.RESOLUTION_LOOP)


def test_symlink_cycle_as_the_root_is_a_resolution_loop(tmp_path):
    _symlink(tmp_path / "b", tmp_path / "a", directory=True)
    _symlink(tmp_path / "a", tmp_path / "b", directory=True)
    _failure(_artifact("stem.wav"), str(tmp_path / "a"), Code.RESOLUTION_LOOP)


def test_dangling_symlink_candidate_is_not_found(tree):
    _symlink(tree / "missing.wav", tree / "dangling.wav", directory=False)
    _failure(_artifact("dangling.wav"), str(tree), Code.NOT_FOUND)


@pytest.mark.skipif(not WINDOWS, reason="directory junctions exist only on Windows")
def test_windows_junction_inside_the_root_to_an_inside_directory_is_allowed(tree):
    _junction(tree / "outputs", tree / "junction")
    record = resolve_artifact_location(_artifact("junction/stem.wav"), root=str(tree))
    assert record.absolute_path == _canonical(tree / "outputs" / "stem.wav")


@pytest.mark.skipif(not WINDOWS, reason="directory junctions exist only on Windows")
def test_windows_junction_inside_the_root_to_an_outside_directory_is_rejected(tree):
    _junction(tree.parent / "root-other", tree / "junction")
    _failure(_artifact("junction/stem.wav"), str(tree), Code.OUTSIDE_ROOT)


@pytest.mark.skipif(not WINDOWS, reason="directory junctions exist only on Windows")
def test_windows_junction_cycle_is_a_resolution_loop(tree):
    # a -> x, then x is replaced by a junction back to a.
    (tree / "x").mkdir()
    _junction(tree / "x", tree / "a")
    (tree / "x").rmdir()
    _junction(tree / "a", tree / "x")
    _failure(_artifact("a/stem.wav"), str(tree), Code.RESOLUTION_LOOP)
    _failure(_artifact("stem.wav"), str(tree / "a"), Code.RESOLUTION_LOOP)


@pytest.mark.skipif(not WINDOWS, reason="directory junctions exist only on Windows")
def test_windows_root_reached_through_a_junction_records_the_actual_directory(tree, tmp_path):
    _junction(tree, tmp_path / "root-junction")
    record = resolve_artifact_location(_artifact("outputs/stem.wav"), root=str(tmp_path / "root-junction"))
    assert record.configured_root == str(tmp_path / "root-junction")
    assert record.resolved_root == _canonical(tree)
    assert record.absolute_path == _canonical(tree / "outputs" / "stem.wav")
