"""Compatibility shim (ADR-027 migration step M3): definitions moved to ``autonomous_mixing.adapters.rendering.dsp``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.adapters.rendering.dsp import _CHANNEL_COUNTS as _CHANNEL_COUNTS
from autonomous_mixing.adapters.rendering.dsp import _gain_factor as _gain_factor
from autonomous_mixing.adapters.rendering.dsp import _require_samples as _require_samples
from autonomous_mixing.adapters.rendering.dsp import apply_absolute_gain, sum_stems

__all__ = ["apply_absolute_gain", "sum_stems"]
