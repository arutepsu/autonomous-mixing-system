"""Compatibility shim (ADR-027 migration step M3): definitions moved to ``autonomous_mixing.adapters.dsp_analysis.analysis``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.adapters.dsp_analysis.analysis import analyze_pair, sha256

__all__ = ["sha256", "analyze_pair"]
