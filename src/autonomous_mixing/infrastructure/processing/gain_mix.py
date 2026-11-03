"""Compatibility shim (ADR-027 migration step M3): definitions moved to ``autonomous_mixing.adapters.rendering.gain_mix``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.adapters.rendering.gain_mix import (
    GAIN_MIX_PROCESSOR,
    INPUT_VERIFICATION_FAILED,
    MIX_FILENAME,
    OUTPUT_FAILED,
    PROCESSING_FAILED,
    TARGET_FILENAME,
    GainMixProcessor,
    ProcessingLimits,
)
from autonomous_mixing.adapters.rendering.gain_mix import _candidate as _candidate
from autonomous_mixing.adapters.rendering.gain_mix import _Failed as _Failed
from autonomous_mixing.adapters.rendering.gain_mix import _output_location as _output_location
from autonomous_mixing.adapters.rendering.gain_mix import _render as _render
from autonomous_mixing.adapters.rendering.gain_mix import _require_compatible as _require_compatible
from autonomous_mixing.adapters.rendering.gain_mix import (
    _require_distinct_output_locations as _require_distinct_output_locations,
)

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.adapters.rendering.gain_mix import _require_limit as _require_limit

__all__ = [
    "GAIN_MIX_PROCESSOR",
    "TARGET_FILENAME",
    "MIX_FILENAME",
    "INPUT_VERIFICATION_FAILED",
    "PROCESSING_FAILED",
    "OUTPUT_FAILED",
    "ProcessingLimits",
    "GainMixProcessor",
]
