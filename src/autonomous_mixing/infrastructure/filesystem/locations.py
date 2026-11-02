"""Read-only resolution of an ``AudioArtifact`` location against an explicit local filesystem root.

The original artifact is never copied or rewritten: the result is a separate record holding the
same artifact object, the caller's root string verbatim, and the strictly resolved root and target
paths. Relative locations are joined to the resolved root; absolute locations are used directly. The
strictly resolved target must be a descendant of the resolved root by path components. Links that
resolve inside the root are allowed; targets outside it, or equal to it, are rejected.

Only native local path syntax of the running platform is interpreted. There is no current-directory
fallback, URI decoding, environment-variable or tilde expansion, or foreign-platform translation.
Resolution inspects filesystem metadata only: it opens no file content and does not check that the
target is a regular file or readable. The checks describe the filesystem observed during the calls;
they do not establish race-free confinement, link immutability, hard-link uniqueness, exclusive
access or write protection.
"""

import errno
import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from autonomous_mixing.domain.project import AudioArtifact

# A URI scheme of two or more characters followed by ":" (one letter plus ":" is a Windows drive).
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]+:")
_DRIVE = re.compile(r"[A-Za-z]:")
_ANY_SEPARATOR = ("/", "\\")
_WINDOWS_SPLIT = re.compile(r"[\\/]")


class LocationResolutionFailureCode(Enum):
    UNSUPPORTED_PATH = "unsupported_path"
    INVALID_ROOT = "invalid_root"
    NOT_FOUND = "not_found"
    NOT_READABLE = "not_readable"
    OUTSIDE_ROOT = "outside_root"
    RESOLUTION_LOOP = "resolution_loop"
    STALE_RESOLUTION = "stale_resolution"


class LocationResolutionError(ValueError):
    """An expected resolution failure; ``code`` names the rejected condition."""

    def __init__(self, code: LocationResolutionFailureCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string, got {value!r}")
    return value


@dataclass(frozen=True)
class ResolvedArtifactLocation:
    """The original artifact with the root it was resolved against and the resulting native paths.

    ``configured_root`` is the caller's root string unchanged; ``resolved_root`` and
    ``absolute_path`` are produced by strict filesystem resolution when the record comes from
    ``resolve_artifact_location``. Construction checks field types and non-blank strings only, without
    filesystem access: a record certifies neither existence nor canonicality, may be stale, and is not
    an authorization or capability.
    """

    artifact: AudioArtifact
    configured_root: str
    resolved_root: str
    absolute_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, AudioArtifact):
            raise ValueError(f"ResolvedArtifactLocation.artifact must be an AudioArtifact, got {self.artifact!r}")
        _require_text(self.configured_root, "ResolvedArtifactLocation.configured_root")
        _require_text(self.resolved_root, "ResolvedArtifactLocation.resolved_root")
        _require_text(self.absolute_path, "ResolvedArtifactLocation.absolute_path")


def _fail(code: LocationResolutionFailureCode, message: str) -> LocationResolutionError:
    return LocationResolutionError(code, message)


def _lexical_problem(text: str, *, windows: bool, require_absolute: bool) -> str | None:
    """Return why ``text`` is unsupported under the Windows or POSIX policy, or None; no filesystem access."""
    if "\x00" in text:
        return "contains a NUL character"
    if text[:1] in _ANY_SEPARATOR and text[1:2] in _ANY_SEPARATOR:
        return "UNC, network or device-namespace paths are not supported"
    if _URI_SCHEME.match(text):
        return "URI-style locations are not supported"
    drive = _DRIVE.match(text)
    if windows:
        rest = text
        if drive:
            if text[2:3] not in _ANY_SEPARATOR:
                return "drive-relative Windows paths are not supported"
            rest = text[2:]
        elif text[:1] in _ANY_SEPARATOR:
            return "root-relative Windows paths are not supported"
        if ":" in rest:
            return "colons outside a drive prefix, including alternate data streams, are not supported"
        absolute = drive is not None
        components = _WINDOWS_SPLIT.split(text)
    else:
        if drive:
            return "Windows drive syntax is not supported on this platform"
        absolute = text.startswith("/")
        components = text.split("/")
    if ".." in components:
        return "parent-directory components are not supported"
    if require_absolute and not absolute:
        return "must be a fully qualified absolute local path"
    return None


def _require_lexical(text: str, *, what: str, require_absolute: bool) -> None:
    problem = _lexical_problem(text, windows=os.name == "nt", require_absolute=require_absolute)
    if problem is not None:
        raise _fail(LocationResolutionFailureCode.UNSUPPORTED_PATH, f"{what} {text!r}: {problem}")


def resolve_artifact_location(artifact: AudioArtifact, *, root: str) -> ResolvedArtifactLocation:
    """Resolve ``artifact.location`` against the explicit ``root`` without opening file content.

    Order: argument checks (plain ``ValueError``); lexical checks of the root, then of the location;
    strict resolution of the root and a directory check; strict resolution of the candidate; component
    containment. The first failure raises ``LocationResolutionError``.
    """
    if not isinstance(artifact, AudioArtifact):
        raise ValueError(f"artifact must be an AudioArtifact, got {type(artifact).__name__}")
    _require_text(root, "root")
    _require_lexical(root, what="root", require_absolute=True)
    _require_lexical(artifact.location, what="artifact location", require_absolute=False)
    resolved_root = _resolve_root(root)
    location = Path(artifact.location)
    candidate = location if location.is_absolute() else resolved_root / location
    resolved = _strict_resolve(candidate, missing=LocationResolutionFailureCode.NOT_FOUND, what="artifact location")
    root_parts, parts = resolved_root.parts, resolved.parts
    if len(parts) <= len(root_parts) or parts[: len(root_parts)] != root_parts:
        raise _fail(
            LocationResolutionFailureCode.OUTSIDE_ROOT,
            f"artifact location {artifact.location!r} resolves to {resolved}, not below root {resolved_root}",
        )
    return ResolvedArtifactLocation(artifact, root, str(resolved_root), str(resolved))


def _strict_resolve(path: Path, *, missing: LocationResolutionFailureCode, what: str) -> Path:
    """Strictly resolve ``path``, translating only the expected missing/permission/loop failures."""
    try:
        return path.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise _fail(missing, f"{what} {str(path)!r} does not exist: {exc.strerror}") from exc
    except PermissionError as exc:
        raise _fail(LocationResolutionFailureCode.NOT_READABLE, f"cannot resolve {what} {str(path)!r}") from exc
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            raise
        raise _fail(LocationResolutionFailureCode.RESOLUTION_LOOP, f"{what} {str(path)!r} has a link cycle") from exc
    except RuntimeError as exc:
        # Python 3.11 pathlib reports a strict-resolution link cycle as a plain RuntimeError; subclasses
        # such as RecursionError and NotImplementedError are not cycle reports and propagate.
        if type(exc) is not RuntimeError:
            raise
        raise _fail(LocationResolutionFailureCode.RESOLUTION_LOOP, f"{what} {str(path)!r} has a link cycle") from exc


def _resolve_root(root: str) -> Path:
    invalid = LocationResolutionFailureCode.INVALID_ROOT
    resolved = _strict_resolve(Path(root), missing=invalid, what="root")
    try:
        mode = os.stat(resolved).st_mode
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise _fail(invalid, f"root {root!r} is no longer present at {resolved}") from exc
    except PermissionError as exc:
        raise _fail(LocationResolutionFailureCode.NOT_READABLE, f"cannot inspect root {root!r}") from exc
    if not stat.S_ISDIR(mode):
        raise _fail(invalid, f"root {root!r} resolves to {resolved}, which is not a directory")
    return resolved
