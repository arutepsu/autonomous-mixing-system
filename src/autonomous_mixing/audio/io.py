"""Compatibility shim (ADR-027 migration step M3): definitions moved to ``autonomous_mixing.adapters.dsp_analysis.io``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.adapters.dsp_analysis.io import WAV_FORMATS, AudioStem, load_stem, validate_pair

__all__ = ["WAV_FORMATS", "AudioStem", "load_stem", "validate_pair"]
