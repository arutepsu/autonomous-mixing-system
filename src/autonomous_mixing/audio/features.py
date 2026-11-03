"""Compatibility shim (ADR-027 migration step M3): definitions moved to ``autonomous_mixing.adapters.dsp_analysis.features``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.adapters.dsp_analysis.features import (
    band_power,
    coactivity_proxy,
    mono,
    peak_dbfs,
    rms_dbfs,
    spectral_features,
)

__all__ = ["mono", "rms_dbfs", "peak_dbfs", "spectral_features", "coactivity_proxy", "band_power"]
