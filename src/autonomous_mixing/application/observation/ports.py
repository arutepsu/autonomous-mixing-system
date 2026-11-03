"""Compatibility shim (ADR-027 migration step M2): definitions moved to ``autonomous_mixing.core.ports.analysis``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.core.ports.analysis import AudioAnalyzer

__all__ = ["AudioAnalyzer"]
