"""Pending DAW export handoff and verified import records (plan 2026-11-18-daw-export-handoff-import, revision 3).

After a confirmed or explicitly resolved fader change, an export request is prepared and *awaits operator export*:
it is never candidate-ready and never accepted. A separate continuation verifies the operator's files and checks
observed state continuity before and after that verification; only the pure core assessment builds
``DawImportVerified``. What these records establish, and what they do not:

- **State anchor.** Journal-backed source evidence: the latest state-establishing ledger event (a confirmed write
  or restoration, or a resolution) and the reading it recorded, with the complete journaled binding and profile.
  Preparation records this evidence only; it captures no fresh inspection.
- **Continuity.** Observed agreement of the journal and the fader at preparation, before the import and after it.
  It does not establish that nothing moved during the export (a manual move and move-back, or a routing or plugin
  change, is undetectable), and a matching fader does not imply matching audio.
- **Timeline.** Declared by the request, physical alignment unverified: rate, channels and frames are verified,
  the origin is provenance only.
- **Files.** A hash establishes file identity only. Names, the operator attestation and the metadata title do not
  authenticate the open project or prove how a file was exported. The master is not assumed to equal the stems.

The continuity, anchor-currency, file-name and candidate-derivation rules live here because record constructors
call them; no record imports a stage module.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.daw_binding import DawFaderReading, DawTrackBinding
from autonomous_mixing.core.records.daw_control import (
    DawApplyRequest,
    DawControlProfile,
    DawInspection,
    DawInspectRequest,
)
from autonomous_mixing.core.records.daw_recovery import DawControlState
from autonomous_mixing.core.records.identifiers import ArtifactId, ExecutionId, ProjectId, Revision, TrackId, VersionRef
from autonomous_mixing.core.records.invariants import (
    require_count,
    require_finite,
    require_instance,
    require_text,
    require_tuple_of,
    require_utc,
)
from autonomous_mixing.core.records.project import ArtifactCheckpoint
from autonomous_mixing.core.records.rendering import ExecutionWorkspace

_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# The reserved request file in every export workspace (compared after NFC normalization and case folding).
DAW_EXPORT_REQUEST_FILE = "export-request.json"
_WINDOWS_INVALID = set('<>:"/\\|?*')
_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                           *(f"LPT{i}" for i in range(1, 10))})  # fmt: skip


def _require_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{field} must be 8-64 characters of A-Z, a-z, 0-9, '_' or '-'")
    return value


def _require_seq(value: object, field: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field} must be a built-in int of at least {minimum}")
    return value


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a bool")
    return value


# --- file names -----------------------------------------------------------------------------------------------------


def daw_file_name_key(name: str) -> str:
    """The comparison key of a workspace file name: NFC, case-folded, NFC again (NTFS compares case-insensitively)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def daw_export_file_name_problem(name: object) -> str | None:
    """Why ``name`` cannot be an exported output's file name, or None. Pure; no filesystem access.

    One path component; not ``.``/``..``; not the reserved request file; no Windows-invalid or control characters;
    no trailing dot or space; not a Windows device name with or without an extension; a ``.wav`` extension.
    """
    if not isinstance(name, str) or not name:
        return "must be a non-empty str"
    if name in (".", ".."):
        return "must not be '.' or '..'"
    if any(char in _WINDOWS_INVALID or ord(char) < 32 or ord(char) == 127 for char in name):
        return "contains a separator, a Windows-invalid character or a control character"
    if name[-1] in (".", " "):
        return "must not end with a dot or a space"
    if daw_file_name_key(name) == daw_file_name_key(DAW_EXPORT_REQUEST_FILE):
        return f"{DAW_EXPORT_REQUEST_FILE} is reserved for the export request"
    if name.split(".", 1)[0].rstrip(" ").upper() in _DEVICE_NAMES:
        return "is a Windows reserved device name"
    if not name.lower().endswith(".wav") or len(name) == len(".wav"):
        return "must have a name and a .wav extension"
    return None


# --- state anchor and continuity -------------------------------------------------------------------------------------


class DawStateBasis(Enum):
    CONFIRMED_WRITE = "confirmed_write"
    CONFIRMED_RESTORE = "confirmed_restore"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class DawStateAnchor:
    """Journal-backed source evidence: the latest state-establishing ledger event and the reading it recorded.

    ``binding`` and ``profile`` are the complete records journaled with the source (for a resolution, with its
    root apply), not references. The watermarks are the highest ``RESERVED`` and ``RESOLVED`` seqs (0 when there
    is none) and the highest seq of any kind, read in the same journal pass. Construction enforces currency: a
    write or restoration source is the latest reservation and no resolution follows it; a resolution source is
    the latest resolution and no reservation follows it. The anchor is evidence of what was journaled, not a fresh
    reading; the journal also refuses an anchor while any command is unresolved.
    """

    source_id: str
    basis: DawStateBasis
    source_seq: int
    execution_id: ExecutionId
    binding: DawTrackBinding
    profile: DawControlProfile
    reading: DawFaderReading
    reservation_watermark: int
    resolution_watermark: int
    ledger_watermark: int
    # Review fix 2026-11-19: the exact journaled apply request of a CONFIRMED_WRITE source, rebuilt from its stored
    # text; None for restoration and resolution sources. Request identity is exact agreement with it, never a tolerance.
    apply: DawApplyRequest | None

    def __post_init__(self) -> None:
        _require_id(self.source_id, "DawStateAnchor.source_id")
        require_instance(self.basis, DawStateBasis, "DawStateAnchor.basis")
        _require_seq(self.source_seq, "DawStateAnchor.source_seq", minimum=1)
        require_instance(self.execution_id, ExecutionId, "DawStateAnchor.execution_id")
        require_instance(self.binding, DawTrackBinding, "DawStateAnchor.binding")
        require_instance(self.profile, DawControlProfile, "DawStateAnchor.profile")
        require_instance(self.reading, DawFaderReading, "DawStateAnchor.reading")
        reserved = _require_seq(self.reservation_watermark, "DawStateAnchor.reservation_watermark", minimum=1)
        resolved = _require_seq(self.resolution_watermark, "DawStateAnchor.resolution_watermark", minimum=0)
        ledger = _require_seq(self.ledger_watermark, "DawStateAnchor.ledger_watermark", minimum=1)
        if not self.profile.covers(self.binding):
            raise ValueError("DawStateAnchor binding lies outside its profile's scope")
        if max(reserved, resolved, self.source_seq) > ledger:
            raise ValueError("DawStateAnchor watermarks and source must not exceed the ledger watermark")
        if self.basis is DawStateBasis.RESOLVED:
            if resolved != self.source_seq or reserved >= self.source_seq:
                raise ValueError("a resolution anchor must be the latest resolution, with no reservation after it")
        elif reserved != self.source_seq or resolved >= self.source_seq:
            raise ValueError("a confirmed-command anchor must be the latest reservation, with no resolution after it")
        if self.basis is not DawStateBasis.CONFIRMED_WRITE:
            if self.apply is not None:
                raise ValueError("only a confirmed-write anchor carries an apply request")
            return
        require_instance(self.apply, DawApplyRequest, "DawStateAnchor.apply")
        apply = self.apply
        if (apply.command_id, apply.execution_id) != (self.source_id, self.execution_id):
            raise ValueError("DawStateAnchor apply request answers another command or execution")
        if (apply.resolution.state.binding, apply.profile) != (self.binding, self.profile):
            raise ValueError("DawStateAnchor apply request used another binding or profile")


@dataclass(frozen=True)
class DawContinuitySnapshot:
    """One coherent capture: the anchor, a fresh inspection and the control state.

    Port obligation (``DawRecoveryController.continuity_snapshot``): the adapter holds its lock across the capture
    and queries the anchor and control state before and after the inspection; it returns a snapshot only when both
    queries agree. The record checks that the inspection answers the anchor's own binding and profile.
    """

    anchor: DawStateAnchor
    inspection: DawInspection
    control_state: DawControlState

    def __post_init__(self) -> None:
        require_instance(self.anchor, DawStateAnchor, "DawContinuitySnapshot.anchor")
        require_instance(self.inspection, DawInspection, "DawContinuitySnapshot.inspection")
        require_instance(self.control_state, DawControlState, "DawContinuitySnapshot.control_state")
        if self.inspection.request != DawInspectRequest(self.anchor.binding, self.anchor.profile):
            raise ValueError("DawContinuitySnapshot inspection answers another binding or profile than its anchor")


class DawImportRejectionCode(Enum):
    REQUEST_MISMATCH = "request_mismatch"
    WORKSPACE_CHANGED = "workspace_changed"
    WORKSPACE_REPLACED = "workspace_replaced"
    STATE_DISCONTINUITY = "state_discontinuity"
    UNRESOLVED_COMMAND = "unresolved_command"
    CONVERSION_BLOCKED = "conversion_blocked"
    INCOMPLETE_COVERAGE = "incomplete_coverage"
    UNEXPECTED_FILE = "unexpected_file"
    NAME_COLLISION = "name_collision"
    LINKED_FILE = "linked_file"
    DUPLICATE_PHYSICAL_FILE = "duplicate_physical_file"
    LOCATION = "location"
    HASH_MISMATCH = "hash_mismatch"
    ENCODING = "encoding"
    DIMENSIONS = "dimensions"
    NONFINITE = "nonfinite"
    RESOURCE_LIMIT = "resource_limit"
    UNAVAILABLE = "unavailable"


def daw_continuity_rejection(
    prepared: DawStateAnchor, snapshot: DawContinuitySnapshot
) -> tuple[DawImportRejectionCode, str] | None:
    """The first continuity failure of ``snapshot`` against the anchor recorded at preparation, or None.

    In order: the anchor equals the prepared one in every field, both watermarks included (no ledger entry of any
    kind since preparation); nothing is unresolved; the conversion is not blocked; the inspection reports the bound
    identity on the tested build; the fader reads exactly the anchor's reading in both coordinates. Passing
    establishes observed agreement only, never that nothing moved in between.
    """
    require_instance(prepared, DawStateAnchor, "prepared")
    require_instance(snapshot, DawContinuitySnapshot, "snapshot")
    if snapshot.anchor != prepared:
        return DawImportRejectionCode.STATE_DISCONTINUITY, "the journal anchor or its ledger changed since preparation"
    if snapshot.control_state.unresolved:
        return DawImportRejectionCode.UNRESOLVED_COMMAND, "a DAW command is unresolved"
    if prepared.profile.conversion in snapshot.control_state.blocked_conversions:
        return DawImportRejectionCode.CONVERSION_BLOCKED, "the anchor's conversion is blocked"
    inspection = snapshot.inspection
    if not inspection.matches_binding or inspection.build != prepared.profile.build:
        return DawImportRejectionCode.STATE_DISCONTINUITY, "the inspection does not confirm the bound insert and build"
    if inspection.fader != prepared.reading:
        return DawImportRejectionCode.STATE_DISCONTINUITY, "the fader does not read exactly the anchored reading"
    return None


# --- the export request ------------------------------------------------------------------------------------------------


class DawDependencyStatus(Enum):
    DECLARED = "declared"
    UNKNOWN = "unknown"


class RoutingUnknownError(ValueError):
    """An export request with undeclared routing dependencies: the affected set cannot be fixed."""


@dataclass(frozen=True)
class DawRoutingDeclaration:
    """The operator's declaration of which other tracks' exports the bound fader affects (besides the master)."""

    reference: VersionRef
    status: DawDependencyStatus
    dependent_track_ids: tuple[TrackId, ...]
    effect_notes: tuple[str, ...]

    def __post_init__(self) -> None:
        require_instance(self.reference, VersionRef, "DawRoutingDeclaration.reference")
        require_instance(self.status, DawDependencyStatus, "DawRoutingDeclaration.status")
        require_tuple_of(self.dependent_track_ids, TrackId, "DawRoutingDeclaration.dependent_track_ids",
                         nonempty=False, distinct=True)  # fmt: skip
        require_tuple_of(self.effect_notes, str, "DawRoutingDeclaration.effect_notes", nonempty=False, distinct=False)
        for note in self.effect_notes:
            require_text(note, "DawRoutingDeclaration.effect_notes[]")


@dataclass(frozen=True)
class DawExportFormat:
    """Exact libsndfile container and subtype names the operator must export (for example ``WAV``, ``PCM_24``)."""

    container: str
    subtype: str

    def __post_init__(self) -> None:
        require_text(self.container, "DawExportFormat.container")
        require_text(self.subtype, "DawExportFormat.subtype")


@dataclass(frozen=True)
class DawExportTimeline:
    """Declared timeline of every output; the origin is provenance, physical alignment is not verified."""

    start_seconds: float
    sample_rate_hz: int
    frame_count: int

    def __post_init__(self) -> None:
        if require_finite(self.start_seconds, "DawExportTimeline.start_seconds") < 0:
            raise ValueError("DawExportTimeline.start_seconds must be >= 0")
        require_count(self.sample_rate_hz, "DawExportTimeline.sample_rate_hz", minimum=1)
        require_count(self.frame_count, "DawExportTimeline.frame_count", minimum=1)


@dataclass(frozen=True)
class DawExportOutput:
    """One required exported file and the before-artifact it explicitly replaces (same kind and source tracks)."""

    artifact_id: ArtifactId
    replaces: ArtifactId
    kind: ArtifactKind
    source_track_ids: tuple[TrackId, ...]
    channel_count: int
    file_name: str

    def __post_init__(self) -> None:
        require_instance(self.artifact_id, ArtifactId, "DawExportOutput.artifact_id")
        require_instance(self.replaces, ArtifactId, "DawExportOutput.replaces")
        require_instance(self.kind, ArtifactKind, "DawExportOutput.kind")
        require_tuple_of(self.source_track_ids, TrackId, "DawExportOutput.source_track_ids", nonempty=True,
                         distinct=True)  # fmt: skip
        if require_count(self.channel_count, "DawExportOutput.channel_count", minimum=1) > 2:
            raise ValueError("DawExportOutput.channel_count must be 1 or 2 (as exported; no conversion)")
        problem = daw_export_file_name_problem(self.file_name)
        if problem is not None:
            raise ValueError(f"DawExportOutput.file_name {self.file_name!r} {problem}")


@dataclass(frozen=True)
class DawExportRequest:
    """The immutable request for one operator export after an anchored DAW state; validated independently.

    Identity: one execution across request, workspace and anchor; the complete binding and profile equal the
    anchor's journaled records; the candidate revision is new. Replacement: each output replaces exactly one
    before-checkpoint member of the same kind and source tracks. Coverage (routing must be declared): exactly the
    target track's stem, one stem per declared dependent, and one master mix; members not replaced are retained.
    """

    export_id: str
    execution_id: ExecutionId
    before_checkpoint: ArtifactCheckpoint
    candidate_revision: Revision
    binding: DawTrackBinding
    profile: DawControlProfile
    anchor: DawStateAnchor
    routing: DawRoutingDeclaration
    outputs: tuple[DawExportOutput, ...]
    timeline: DawExportTimeline
    format: DawExportFormat
    render_spec: VersionRef
    workspace: ExecutionWorkspace

    def __post_init__(self) -> None:
        _require_id(self.export_id, "DawExportRequest.export_id")
        require_instance(self.execution_id, ExecutionId, "DawExportRequest.execution_id")
        require_instance(self.before_checkpoint, ArtifactCheckpoint, "DawExportRequest.before_checkpoint")
        require_instance(self.candidate_revision, Revision, "DawExportRequest.candidate_revision")
        require_instance(self.binding, DawTrackBinding, "DawExportRequest.binding")
        require_instance(self.profile, DawControlProfile, "DawExportRequest.profile")
        require_instance(self.anchor, DawStateAnchor, "DawExportRequest.anchor")
        require_instance(self.routing, DawRoutingDeclaration, "DawExportRequest.routing")
        require_tuple_of(self.outputs, DawExportOutput, "DawExportRequest.outputs", nonempty=True, distinct=False)
        require_instance(self.timeline, DawExportTimeline, "DawExportRequest.timeline")
        require_instance(self.format, DawExportFormat, "DawExportRequest.format")
        require_instance(self.render_spec, VersionRef, "DawExportRequest.render_spec")
        require_instance(self.workspace, ExecutionWorkspace, "DawExportRequest.workspace")
        self._check_identity()
        self._check_outputs()

    @property
    def project_id(self) -> ProjectId:
        return self.before_checkpoint.project_id

    def _check_identity(self) -> None:
        if not (self.workspace.execution_id == self.execution_id == self.anchor.execution_id):
            raise ValueError("DawExportRequest execution IDs of request, workspace and anchor differ")
        if self.binding != self.anchor.binding:
            raise ValueError("DawExportRequest binding differs from the anchor's journaled binding")
        if self.profile != self.anchor.profile:
            raise ValueError("DawExportRequest profile differs from the anchor's journaled profile")
        if self.binding.project_id != self.project_id:
            raise ValueError("DawExportRequest binding belongs to another project than the before checkpoint")
        if self.candidate_revision in self.before_checkpoint.known_revisions:
            raise ValueError("DawExportRequest.candidate_revision must be new to the before checkpoint")

    def _check_outputs(self) -> None:
        problem = daw_export_coverage_problem(self.before_checkpoint, self.binding.track_id, self.routing, self.outputs)
        if problem is not None:
            raise ValueError(f"DawExportRequest {problem}")


def daw_export_coverage_problem(
    before_checkpoint: ArtifactCheckpoint,
    target: TrackId,
    routing: DawRoutingDeclaration,
    outputs: tuple[DawExportOutput, ...],
) -> str | None:
    """Why ``outputs`` do not exactly cover and explicitly replace the affected set of ``target``, or None.

    Shared by ``DawExportRequest`` and the coordinator's ``DawCorrectionPlan`` so both decide identically. Unknown
    routing raises ``RoutingUnknownError``. Each output replaces one distinct before member of the same kind and
    source tracks; the stems are exactly the target and the declared dependents; exactly one master mix. Every before
    member whose sources include the target must be replaced, and a group containing the target is unsupported
    (ADR-035, G5): this slice exports stems and the master only.
    """
    if routing.status is not DawDependencyStatus.DECLARED:
        raise RoutingUnknownError("routing dependencies must be declared, not unknown")
    if target in routing.dependent_track_ids:
        return "routing lists the bound track as its own dependent"
    ids = [output.artifact_id for output in outputs]
    replaced = [output.replaces for output in outputs]
    keys = [daw_file_name_key(output.file_name) for output in outputs]
    if len(set(ids)) != len(ids) or len(set(replaced)) != len(replaced):
        return "output IDs and replaced IDs must each be distinct"
    if set(ids) & before_checkpoint.artifact_ids:
        return "output IDs must be new to the before checkpoint"
    if len(set(keys)) != len(keys):
        return "output file names collide after normalization and case folding"
    stems, mixes = [], 0
    for output in outputs:
        before = before_checkpoint.artifact_for(output.replaces)
        if before is None:
            return f"output {output.artifact_id} replaces a non-member {output.replaces}"
        if (before.kind, before.source_track_ids) != (output.kind, output.source_track_ids):
            return f"output {output.artifact_id} differs in kind or tracks from what it replaces"
        if output.kind is ArtifactKind.STEM:
            if len(output.source_track_ids) != 1:
                return "a stem output has exactly one source track"
            stems.append(output.source_track_ids[0])
        elif output.kind is ArtifactKind.MIX:
            mixes += 1
        else:
            return f"output {output.artifact_id} is outside the affected set"
    if mixes != 1:
        return "must export exactly one master mix"
    if sorted(stems, key=str) != sorted([target, *routing.dependent_track_ids], key=str):
        return "stems must be exactly the target track and the declared dependents"
    for member in before_checkpoint.artifacts:
        if target not in member.source_track_ids:
            continue
        if member.kind is ArtifactKind.GROUP:
            return f"before member {member.artifact_id} is a group containing the target; groups are unsupported"
        if member.artifact_id not in replaced:
            return f"before member {member.artifact_id} contains the target but is not replaced"
    return None


def daw_export_artifact(request: DawExportRequest, output: DawExportOutput, content_hash: str) -> AudioArtifact:
    """The candidate artifact one output becomes: declared timeline and format, workspace-relative location."""
    require_instance(request, DawExportRequest, "request")
    if output not in request.outputs:
        raise ValueError("the output does not belong to the request")
    return AudioArtifact(
        output.artifact_id,
        request.project_id,
        request.candidate_revision,
        output.source_track_ids,
        output.kind,
        f"{request.workspace.directory_token}/{output.file_name}",
        request.timeline.start_seconds,
        request.timeline.sample_rate_hz,
        output.channel_count,
        request.timeline.frame_count,
        content_hash,
        request.render_spec,
    )


# --- preparation ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DawFileIdentity:
    """File-system identity of one object as reported by ``stat`` (volume serial and file index on NTFS)."""

    device: int
    index: int

    def __post_init__(self) -> None:
        _require_seq(self.device, "DawFileIdentity.device", minimum=0)
        _require_seq(self.index, "DawFileIdentity.index", minimum=0)


@dataclass(frozen=True)
class DawExportAwaiting:
    """The request is persisted and awaits operator export; never candidate-ready, never accepted.

    It records journal-backed source evidence (the request's anchor), not a fresh inspection, plus the workspace's
    and request file's file-system identity and the request file's exact size and SHA-256. Identity values can be
    reused after deletion and are checked without race-freedom: they detect replacement, not every modification.
    """

    request: DawExportRequest
    request_fingerprint: str
    request_size: int
    resolved_root: str
    workspace_directory: str
    directory_identity: DawFileIdentity
    request_file_identity: DawFileIdentity

    def __post_init__(self) -> None:
        require_instance(self.request, DawExportRequest, "DawExportAwaiting.request")
        _require_sha256(self.request_fingerprint, "DawExportAwaiting.request_fingerprint")
        _require_seq(self.request_size, "DawExportAwaiting.request_size", minimum=1)
        require_text(self.resolved_root, "DawExportAwaiting.resolved_root")
        require_text(self.workspace_directory, "DawExportAwaiting.workspace_directory")
        require_instance(self.directory_identity, DawFileIdentity, "DawExportAwaiting.directory_identity")
        require_instance(self.request_file_identity, DawFileIdentity, "DawExportAwaiting.request_file_identity")
        if self.directory_identity == self.request_file_identity:
            raise ValueError("DawExportAwaiting directory and request file cannot share one identity")


class DawExportPreparationCode(Enum):
    WORKSPACE_UNAVAILABLE = "workspace_unavailable"
    WORKSPACE_OVERLAPS_PROTECTED = "workspace_overlaps_protected"
    REQUEST_NOT_PERSISTED = "request_not_persisted"
    REQUEST_STATE_MISMATCH = "request_state_mismatch"


@dataclass(frozen=True)
class DawExportPreparationFailure:
    export_id: str
    code: DawExportPreparationCode
    explanation: str

    def __post_init__(self) -> None:
        _require_id(self.export_id, "DawExportPreparationFailure.export_id")
        require_instance(self.code, DawExportPreparationCode, "DawExportPreparationFailure.code")
        require_text(self.explanation, "DawExportPreparationFailure.explanation")


# --- the operator's manifest and the file import ------------------------------------------------------------------------


@dataclass(frozen=True)
class DawExportedFile:
    artifact_id: ArtifactId
    file_name: str
    content_hash: str

    def __post_init__(self) -> None:
        require_instance(self.artifact_id, ArtifactId, "DawExportedFile.artifact_id")
        require_text(self.file_name, "DawExportedFile.file_name")
        _require_sha256(self.content_hash, "DawExportedFile.content_hash")


@dataclass(frozen=True)
class DawExportManifest:
    """The operator's attestation of what was exported; it authenticates nothing by itself."""

    export_id: str
    request_fingerprint: str
    files: tuple[DawExportedFile, ...]
    attested_by: VersionRef
    attested_at_utc: datetime
    attestation: str

    def __post_init__(self) -> None:
        _require_id(self.export_id, "DawExportManifest.export_id")
        _require_sha256(self.request_fingerprint, "DawExportManifest.request_fingerprint")
        require_tuple_of(self.files, DawExportedFile, "DawExportManifest.files", nonempty=True, distinct=False)
        ids = [item.artifact_id for item in self.files]
        if len(set(ids)) != len(ids):
            raise ValueError("DawExportManifest.files must have distinct artifact IDs")
        require_instance(self.attested_by, VersionRef, "DawExportManifest.attested_by")
        require_utc(self.attested_at_utc, "DawExportManifest.attested_at_utc")
        require_text(self.attestation, "DawExportManifest.attestation")

    def file_for(self, artifact_id: ArtifactId) -> DawExportedFile | None:
        return next((item for item in self.files if item.artifact_id == artifact_id), None)


@dataclass(frozen=True)
class DawImportRequest:
    """Files only: the awaiting record and the operator's manifest. No DAW input; continuity is checked in core."""

    awaiting: DawExportAwaiting
    manifest: DawExportManifest

    def __post_init__(self) -> None:
        require_instance(self.awaiting, DawExportAwaiting, "DawImportRequest.awaiting")
        require_instance(self.manifest, DawExportManifest, "DawImportRequest.manifest")


@dataclass(frozen=True)
class DawImportedFileEvidence:
    artifact_id: ArtifactId
    verified_sha256: str
    container: str
    subtype: str
    finite_samples: bool
    file_identity: DawFileIdentity

    def __post_init__(self) -> None:
        require_instance(self.artifact_id, ArtifactId, "DawImportedFileEvidence.artifact_id")
        _require_sha256(self.verified_sha256, "DawImportedFileEvidence.verified_sha256")
        require_text(self.container, "DawImportedFileEvidence.container")
        require_text(self.subtype, "DawImportedFileEvidence.subtype")
        _require_bool(self.finite_samples, "DawImportedFileEvidence.finite_samples")
        require_instance(self.file_identity, DawFileIdentity, "DawImportedFileEvidence.file_identity")


@dataclass(frozen=True)
class DawFilesVerified:
    """The adapter's file-level result. Never candidate evidence on its own: continuity is assessed in core.

    Evidence follows the request's outputs one to one and in order; every hash equals the manifest's; container
    and subtype equal the requested format; samples are finite; physical identities are distinct.
    """

    request: DawImportRequest
    evidence: tuple[DawImportedFileEvidence, ...]

    def __post_init__(self) -> None:
        require_instance(self.request, DawImportRequest, "DawFilesVerified.request")
        require_tuple_of(self.evidence, DawImportedFileEvidence, "DawFilesVerified.evidence", nonempty=True,
                         distinct=False)  # fmt: skip
        export = self.request.awaiting.request
        manifest = self.request.manifest
        if (manifest.export_id, manifest.request_fingerprint) != (
            export.export_id,
            self.request.awaiting.request_fingerprint,
        ):
            raise ValueError("DawFilesVerified manifest answers another request")  # fmt: skip
        if len(self.evidence) != len(export.outputs) or len(manifest.files) != len(export.outputs):
            raise ValueError("DawFilesVerified must cover every output exactly once")
        identities = [item.file_identity for item in self.evidence]
        if len(set(identities)) != len(identities) or self.request.awaiting.request_file_identity in identities:
            raise ValueError("DawFilesVerified physical file identities must be distinct")
        for output, item in zip(export.outputs, self.evidence, strict=True):
            listed = manifest.file_for(output.artifact_id)
            if item.artifact_id != output.artifact_id or listed is None or listed.file_name != output.file_name:
                raise ValueError("DawFilesVerified evidence and manifest must follow the request's outputs")
            if item.verified_sha256 != listed.content_hash:
                raise ValueError("DawFilesVerified hash differs from the manifest's")
            if (item.container, item.subtype) != (export.format.container, export.format.subtype):
                raise ValueError("DawFilesVerified format differs from the requested format")
            if not item.finite_samples:
                raise ValueError("DawFilesVerified requires finite samples")


# --- the verified import -----------------------------------------------------------------------------------------------


class DawAlignmentStatus(Enum):
    DECLARED_TIMELINE_PHYSICAL_UNVERIFIED = "declared_timeline_physical_unverified"


@dataclass(frozen=True)
class DawImportVerified:
    """Candidate evidence: verified files plus observed continuity before and after their verification.

    Built only by ``core.admission.daw_export.assess_daw_import``. It is not an ``ExecutionReceipt``, not a
    candidate checkpoint and not accepted: the coordinator builds those after its own checks. ``alignment`` records
    that the timeline is declared and physical alignment unverified.
    """

    files: DawFilesVerified
    candidate_artifacts: tuple[AudioArtifact, ...]
    replacements: tuple[tuple[ArtifactId, ArtifactId], ...]
    before_import: DawContinuitySnapshot
    after_import: DawContinuitySnapshot
    alignment: DawAlignmentStatus

    def __post_init__(self) -> None:
        require_instance(self.files, DawFilesVerified, "DawImportVerified.files")
        require_instance(self.before_import, DawContinuitySnapshot, "DawImportVerified.before_import")
        require_instance(self.after_import, DawContinuitySnapshot, "DawImportVerified.after_import")
        require_instance(self.alignment, DawAlignmentStatus, "DawImportVerified.alignment")
        request = self.files.request.awaiting.request
        expected = tuple(
            daw_export_artifact(request, output, item.verified_sha256)
            for output, item in zip(request.outputs, self.files.evidence, strict=True)
        )
        if self.candidate_artifacts != expected:
            raise ValueError("DawImportVerified artifacts must be derived from the outputs and verified hashes")
        if self.replacements != tuple((output.artifact_id, output.replaces) for output in request.outputs):
            raise ValueError("DawImportVerified replacements must equal the outputs' explicit mapping")
        for name in ("before_import", "after_import"):
            if daw_continuity_rejection(request.anchor, getattr(self, name)) is not None:
                raise ValueError(f"DawImportVerified.{name} does not satisfy the continuity rule")

    @property
    def request(self) -> DawExportRequest:
        return self.files.request.awaiting.request


@dataclass(frozen=True)
class DawImportRejected:
    export_id: str
    code: DawImportRejectionCode
    explanation: str

    def __post_init__(self) -> None:
        _require_id(self.export_id, "DawImportRejected.export_id")
        require_instance(self.code, DawImportRejectionCode, "DawImportRejected.code")
        require_text(self.explanation, "DawImportRejected.explanation")
