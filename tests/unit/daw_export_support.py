"""Test-only export-handoff records: one project with a target, a declared dependent, an unaffected track and a master.

Metadata only, except ``write_wav``, which writes tiny synthetic PCM files for unit tests (never evaluation data).
"""

import hashlib
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import soundfile as sf
from fl_runtime_support import make_request

from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.daw_binding import DawFaderReading
from autonomous_mixing.core.records.daw_control import DawInspection, DawInspectRequest
from autonomous_mixing.core.records.daw_export import (
    DawContinuitySnapshot,
    DawDependencyStatus,
    DawExportAwaiting,
    DawExportedFile,
    DawExportFormat,
    DawExportManifest,
    DawExportOutput,
    DawExportRequest,
    DawExportTimeline,
    DawFileIdentity,
    DawFilesVerified,
    DawImportedFileEvidence,
    DawImportRequest,
    DawRoutingDeclaration,
    DawStateAnchor,
    DawStateBasis,
)
from autonomous_mixing.core.records.daw_recovery import DawControlState
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ExecutionId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.rendering import ExecutionWorkspace

APPLY = make_request("apply_root_0001")
BINDING, PROFILE = APPLY.resolution.state.binding, APPLY.profile
T1, T2, T3 = TrackId("t1"), TrackId("t2"), TrackId("t3")
READING = DawFaderReading(0.7257362319041692, -2.152741670608521)
FORMAT = DawExportFormat("WAV", "PCM_24")
TIMELINE = DawExportTimeline(0.0, 44100, 8)
TOKEN = "export_ws_0001"
ROOT_KEY = "exports"


def before_artifact(artifact_id, tracks, kind=ArtifactKind.STEM):
    return AudioArtifact(ArtifactId(artifact_id), ProjectId("p1"), Revision("r1"), tracks, kind, f"{artifact_id}.wav",
                         0.0, 44100, 1, 8, "0" * 64, VersionRef("render", "1"))  # fmt: skip


S1, S2, S3 = before_artifact("s1", (T1,)), before_artifact("s2", (T2,)), before_artifact("s3", (T3,))
M1 = before_artifact("m1", (T1, T2, T3), ArtifactKind.MIX)
CHECKPOINT = ArtifactCheckpoint(CheckpointId("c1"), ProjectId("p1"), Revision("r1"), (S1, S2, S3, M1))


def state(checkpoint=CHECKPOINT):
    snapshot = ProjectSnapshot(ProjectId("p1"), Revision("r1"), datetime(2026, 11, 18, tzinfo=timezone.utc),
                               (Track(T1, "bass"), Track(T2, "pad"), Track(T3, "kick")))  # fmt: skip
    gains = tuple(TrackGainState(a.source_track_ids[0], a, 0.0) for a in (S1, S2, S3))
    return ProjectState(snapshot, ProjectProcessingState(ProjectId("p1"), Revision("r1"), gains), checkpoint)


def anchor(**changes):
    base = DawStateAnchor("apply_root_0001", DawStateBasis.CONFIRMED_WRITE, 1, ExecutionId("e1"), BINDING, PROFILE,
                          READING, 1, 0, 1, APPLY)  # fmt: skip
    return replace(base, **changes)


def outputs():
    return (
        DawExportOutput(ArtifactId("x1"), ArtifactId("s1"), ArtifactKind.STEM, (T1,), 2, "bass.wav"),
        DawExportOutput(ArtifactId("x2"), ArtifactId("s2"), ArtifactKind.STEM, (T2,), 2, "pad.wav"),
        DawExportOutput(ArtifactId("xm"), ArtifactId("m1"), ArtifactKind.MIX, (T1, T2, T3), 2, "master.wav"),
    )


def routing(status=DawDependencyStatus.DECLARED, dependents=(T2,)):
    return DawRoutingDeclaration(VersionRef("routing", "1"), status, dependents, ())


def export_request(**changes):
    base = DawExportRequest("export_0001", ExecutionId("e1"), CHECKPOINT, Revision("r2"), BINDING, PROFILE, anchor(),
                            routing(), outputs(), TIMELINE, FORMAT, VersionRef("fl-export", "1"),
                            ExecutionWorkspace(ExecutionId("e1"), ROOT_KEY, TOKEN))  # fmt: skip
    return replace(base, **changes)


def awaiting(request=None, fingerprint="a" * 64):
    return DawExportAwaiting(request or export_request(), fingerprint, 100, "C:\\exports", "C:\\exports\\" + TOKEN,
                             DawFileIdentity(7, 1), DawFileIdentity(7, 2))  # fmt: skip


def digest(n):
    return hashlib.sha256(str(n).encode()).hexdigest()


def manifest(awaiting_record, files=None):
    request = awaiting_record.request
    listed = files or tuple(
        DawExportedFile(o.artifact_id, o.file_name, digest(i)) for i, o in enumerate(request.outputs)
    )
    return DawExportManifest(request.export_id, awaiting_record.request_fingerprint, listed, VersionRef("operator", "1"),
                             datetime(2026, 11, 18, 12, tzinfo=timezone.utc), "exported as requested")  # fmt: skip


def files_verified(awaiting_record=None):
    awaiting_record = awaiting_record or awaiting()
    listed = manifest(awaiting_record)
    evidence = tuple(DawImportedFileEvidence(item.artifact_id, item.content_hash, "WAV", "PCM_24", True,
                                             DawFileIdentity(7, 10 + i)) for i, item in enumerate(listed.files))  # fmt: skip
    return DawFilesVerified(DawImportRequest(awaiting_record, listed), evidence)


def inspection(fader=READING, build=None, title=None, binding=BINDING, profile=PROFILE):
    return DawInspection(DawInspectRequest(binding, profile), build or profile.build, title or binding.project_title,
                         binding.insert_index, binding.insert_name, fader)  # fmt: skip


def snapshot(anchor_record=None, fader=READING, state_record=None, **inspect):
    anchor_record = anchor_record or anchor()
    return DawContinuitySnapshot(anchor_record, inspection(fader, binding=anchor_record.binding,
                                                           profile=anchor_record.profile, **inspect),
                                 state_record or DawControlState((), frozenset(), ()))  # fmt: skip


def write_wav(path, *, channels=2, rate=44100, frames=8, subtype="PCM_24", values=None):
    """A tiny deterministic synthetic WAV (unit tests only); returns its SHA-256."""
    data = values if values is not None else np.linspace(-0.5, 0.5, frames * channels).reshape(frames, channels)
    sf.write(str(path), data, rate, subtype=subtype, format="WAV")
    return hashlib.sha256(path.read_bytes()).hexdigest()
