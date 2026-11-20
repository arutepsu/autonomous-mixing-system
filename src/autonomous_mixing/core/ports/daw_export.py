"""The DAW export handoff port and the structural checks on its responses (plan 2026-11-18, revision 3).

Obligations on every implementation:

- ``prepare`` persists the request exclusively in a fresh workspace that overlaps no protected location, writes
  no audio and starts no export; its result awaits the operator and is never a render success or candidate-ready;
- ``import_files`` reads only the awaiting workspace, writes nothing, converts nothing and never contacts the DAW;
  a ``DawFilesVerified`` is never candidate evidence on its own (``core.admission.daw_export`` combines it with
  continuity snapshots).
"""

from typing import Protocol

from autonomous_mixing.core.records.daw_export import (
    DawExportAwaiting,
    DawExportPreparationFailure,
    DawExportRequest,
    DawFilesVerified,
    DawImportRejected,
    DawImportRequest,
)
from autonomous_mixing.core.records.invariants import require_instance


class DawExportHandoff(Protocol):
    """Pending-export preparation and verified file import; no DAW, rendering or acceptance authority."""

    def prepare(self, request: DawExportRequest) -> DawExportAwaiting | DawExportPreparationFailure: ...

    def import_files(self, request: DawImportRequest) -> DawFilesVerified | DawImportRejected: ...


def validate_daw_export_preparation(
    request: DawExportRequest, result: DawExportAwaiting | DawExportPreparationFailure
) -> None:
    require_instance(request, DawExportRequest, "request")
    if isinstance(result, DawExportPreparationFailure):
        if result.export_id != request.export_id:
            raise ValueError("the preparation failure answers another export")
        return
    if not isinstance(result, DawExportAwaiting):
        raise ValueError("a preparation response must be DawExportAwaiting or DawExportPreparationFailure")
    if result.request != request:
        raise ValueError("the awaiting record holds another request")


def validate_daw_files_verified(request: DawImportRequest, result: DawFilesVerified | DawImportRejected) -> None:
    require_instance(request, DawImportRequest, "request")
    if isinstance(result, DawImportRejected):
        if result.export_id != request.awaiting.request.export_id:
            raise ValueError("the import rejection answers another export")
        return
    if not isinstance(result, DawFilesVerified):
        raise ValueError("an import response must be DawFilesVerified or DawImportRejected")
    if result.request != request:
        raise ValueError("the verified files answer another import request")
