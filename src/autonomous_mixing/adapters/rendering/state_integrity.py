"""File-backed ``StateIntegrityInspector``: one read-only pass over every artifact a state requires.

Each required artifact (current audio, then fixed gain inputs not already present) is resolved against the
physical root configured for its logical root key with ``resolve_artifact_location`` and verified with
``check_resolved_wav_integrity`` (bounded single capture, SHA-256, WAV format and declared metadata,
finite samples). Expected resolution and integrity failures become per-artifact FAILED or UNCERTAIN
results and inspection continues; unexpected errors propagate. Samples stay local to one iteration.

The report describes the bytes and metadata captured during these calls. It does not establish
race-free confinement, later file immutability, signal alignment, restoration or journal authority, and
it has no wall-clock bound: limits are per file. Nothing is written, renamed, deleted or retried.
"""

from collections.abc import Mapping
from typing import assert_never

from autonomous_mixing.adapters.rendering.integrity import (
    IntegrityError,
    IntegrityFailureCode,
    _require_limit,
    check_resolved_wav_integrity,
)
from autonomous_mixing.adapters.rendering.locations import (
    LocationResolutionError,
    LocationResolutionFailureCode,
    resolve_artifact_location,
)
from autonomous_mixing.core.records.artifacts import AudioArtifact
from autonomous_mixing.core.records.integrity import (
    ArtifactIntegrityResult,
    IntegrityStatus,
    StateIntegrityReport,
    StateIntegrityRequest,
)
from autonomous_mixing.core.records.invariants import require_instance, require_text


class FileStateIntegrityInspector:
    """Implements ``StateIntegrityInspector`` over configured filesystem roots.

    ``roots`` maps logical root keys to physical root paths and is copied at construction; nothing is
    accessed on the filesystem until ``inspect``. ``max_file_bytes`` and ``max_decoded_bytes`` bound each
    file separately, exactly as in the WAV verifier; they are caller-supplied, not scientific thresholds.
    """

    def __init__(self, roots: Mapping[str, str], *, max_file_bytes: int, max_decoded_bytes: int) -> None:
        if not isinstance(roots, Mapping):
            raise ValueError(f"roots must be a Mapping, got {type(roots).__name__}")
        snapshot = dict(roots.items())
        for key, path in snapshot.items():
            require_text(key, "root key")
            require_text(path, f"root path for {key!r}")
        self._roots: dict[str, str] = snapshot
        self._max_file_bytes = _require_limit(max_file_bytes, "max_file_bytes")
        self._max_decoded_bytes = _require_limit(max_decoded_bytes, "max_decoded_bytes")

    def inspect(self, request: StateIntegrityRequest) -> StateIntegrityReport:
        """Inspect every required artifact once, in ``required_artifacts`` order, and report each."""
        require_instance(request, StateIntegrityRequest, "request")
        root_keys = {binding.artifact_id: binding.root_key for binding in request.bindings}
        results = tuple(
            self._inspect_artifact(artifact, root_keys[artifact.artifact_id]) for artifact in request.required_artifacts
        )
        return StateIntegrityReport(request, results)

    def _inspect_artifact(self, artifact: AudioArtifact, root_key: str) -> ArtifactIntegrityResult:
        root = self._roots.get(root_key)
        if root is None:
            return ArtifactIntegrityResult(
                artifact, IntegrityStatus.UNCERTAIN, None, f"root key {root_key!r} is not configured for this inspector"
            )
        try:
            location = resolve_artifact_location(artifact, root=root)
            checked = check_resolved_wav_integrity(
                location, max_file_bytes=self._max_file_bytes, max_decoded_bytes=self._max_decoded_bytes
            )
        except LocationResolutionError as error:
            return ArtifactIntegrityResult(
                artifact, _location_status(error.code), None, f"location resolution under {root_key!r}: {error}"
            )
        except IntegrityError as error:
            return ArtifactIntegrityResult(
                artifact, _integrity_status(error.code), None, f"WAV verification under {root_key!r}: {error}"
            )
        return ArtifactIntegrityResult(
            artifact,
            IntegrityStatus.VERIFIED,
            checked.verified_sha256,
            f"resolved under {root_key!r}; SHA-256, {checked.container}/{checked.subtype} format and declared "
            "metadata verified; samples finite",
        )


def _location_status(code: LocationResolutionFailureCode) -> IntegrityStatus:
    """FAILED: a concrete configured check rejected the artifact. UNCERTAIN: integrity could not be established."""
    match code:
        case (
            LocationResolutionFailureCode.NOT_FOUND
            | LocationResolutionFailureCode.UNSUPPORTED_PATH
            | LocationResolutionFailureCode.OUTSIDE_ROOT
            | LocationResolutionFailureCode.RESOLUTION_LOOP
        ):
            return IntegrityStatus.FAILED
        case (
            LocationResolutionFailureCode.INVALID_ROOT
            | LocationResolutionFailureCode.NOT_READABLE
            | LocationResolutionFailureCode.STALE_RESOLUTION
        ):
            return IntegrityStatus.UNCERTAIN
        case _:
            assert_never(code)


def _integrity_status(code: IntegrityFailureCode) -> IntegrityStatus:
    match code:
        case (
            IntegrityFailureCode.NOT_FOUND
            | IntegrityFailureCode.UNSUPPORTED_PATH
            | IntegrityFailureCode.NOT_REGULAR_FILE
            | IntegrityFailureCode.HASH_MISMATCH
            | IntegrityFailureCode.INVALID_WAV
            | IntegrityFailureCode.METADATA_MISMATCH
            | IntegrityFailureCode.NONFINITE_SAMPLES
        ):
            return IntegrityStatus.FAILED
        case IntegrityFailureCode.NOT_READABLE | IntegrityFailureCode.RESOURCE_LIMIT:
            return IntegrityStatus.UNCERTAIN
        case _:
            assert_never(code)
