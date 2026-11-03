"""Compatibility shim (ADR-027 migration step M2): definitions moved to ``autonomous_mixing.core.admission.preflight``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.admission.preflight import _dimensions as _dimensions
from autonomous_mixing.core.admission.preflight import preflight_execution

# Records moved in step M1 stay reachable at this legacy path until migration step M4.
from autonomous_mixing.core.records.admission import MetadataReady as MetadataReady
from autonomous_mixing.core.records.admission import PreflightCode as PreflightCode
from autonomous_mixing.core.records.admission import PreflightRejected as PreflightRejected
from autonomous_mixing.core.records.admission import PreflightResult as PreflightResult

__all__ = ["preflight_execution"]
