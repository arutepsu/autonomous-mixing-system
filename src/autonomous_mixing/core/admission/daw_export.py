"""The pure import assessment: verified files and two coherent continuity snapshots become candidate evidence.

Plan 2026-11-18-daw-export-handoff-import (revision 3). The file adapter never talks to the DAW and the DAW adapter
never reads exported audio; only this rule combines their results. Order (the first failure decides): a file-level
rejection passes through unchanged; continuity of ``before_import``; continuity of ``after_import``; otherwise
``DawImportVerified`` with artifacts derived from the request's outputs and the verified hashes. Continuity means
observed agreement at those captures only (``core.records.daw_export.daw_continuity_rejection``).
"""

from autonomous_mixing.core.records.daw_export import (
    DawAlignmentStatus,
    DawContinuitySnapshot,
    DawExportAwaiting,
    DawFilesVerified,
    DawImportRejected,
    DawImportVerified,
    daw_continuity_rejection,
    daw_export_artifact,
)
from autonomous_mixing.core.records.invariants import require_instance


def assess_daw_import(
    awaiting: DawExportAwaiting,
    files: DawFilesVerified | DawImportRejected,
    *,
    before_import: DawContinuitySnapshot,
    after_import: DawContinuitySnapshot,
) -> DawImportVerified | DawImportRejected:
    """Combine the file result with the snapshots taken immediately before and after it (wiring errors raise)."""
    require_instance(awaiting, DawExportAwaiting, "awaiting")
    require_instance(before_import, DawContinuitySnapshot, "before_import")
    require_instance(after_import, DawContinuitySnapshot, "after_import")
    export = awaiting.request
    match files:
        case DawImportRejected():
            if files.export_id != export.export_id:
                raise ValueError("the file rejection answers another export")
            return files
        case DawFilesVerified():
            if files.request.awaiting != awaiting:
                raise ValueError("the verified files answer another awaiting export")
        case _:
            raise ValueError(f"unknown file import result {files!r}")
    for snapshot in (before_import, after_import):
        rejection = daw_continuity_rejection(export.anchor, snapshot)
        if rejection is not None:
            code, explanation = rejection
            when = "before" if snapshot is before_import else "after"
            return DawImportRejected(export.export_id, code, f"{when} the import: {explanation}")
    artifacts = tuple(
        daw_export_artifact(export, output, item.verified_sha256)
        for output, item in zip(export.outputs, files.evidence, strict=True)
    )
    replacements = tuple((output.artifact_id, output.replaces) for output in export.outputs)
    return DawImportVerified(files, artifacts, replacements, before_import, after_import,
                             DawAlignmentStatus.DECLARED_TIMELINE_PHYSICAL_UNVERIFIED)  # fmt: skip
