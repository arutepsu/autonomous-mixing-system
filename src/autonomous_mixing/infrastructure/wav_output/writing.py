"""Compatibility shim (ADR-027 migration step M3): definitions moved to ``autonomous_mixing.adapters.rendering.wav_output``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.adapters.rendering.wav_output import _CHANNEL_COUNTS as _CHANNEL_COUNTS

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.adapters.rendering.wav_output import _FILENAME as _FILENAME
from autonomous_mixing.adapters.rendering.wav_output import _FLOAT64_BYTES as _FLOAT64_BYTES
from autonomous_mixing.adapters.rendering.wav_output import _FORMATS as _FORMATS
from autonomous_mixing.adapters.rendering.wav_output import _IO_CHUNK_BYTES as _IO_CHUNK_BYTES
from autonomous_mixing.adapters.rendering.wav_output import _MAX_SAMPLE_RATE as _MAX_SAMPLE_RATE
from autonomous_mixing.adapters.rendering.wav_output import _RIFF_CHUNK_SIZE_MAX as _RIFF_CHUNK_SIZE_MAX
from autonomous_mixing.adapters.rendering.wav_output import _RIFF_HEADER_BYTES as _RIFF_HEADER_BYTES
from autonomous_mixing.adapters.rendering.wav_output import _SHA256 as _SHA256
from autonomous_mixing.adapters.rendering.wav_output import _SUBTYPES as _SUBTYPES
from autonomous_mixing.adapters.rendering.wav_output import (
    WavOutputError,
    WavOutputFailureCode,
    WrittenWav,
    write_wav_output,
)
from autonomous_mixing.adapters.rendering.wav_output import _close_after_failure as _close_after_failure
from autonomous_mixing.adapters.rendering.wav_output import _Code as _Code
from autonomous_mixing.adapters.rendering.wav_output import _encode as _encode
from autonomous_mixing.adapters.rendering.wav_output import _entry_exists as _entry_exists
from autonomous_mixing.adapters.rendering.wav_output import _filename_problem as _filename_problem
from autonomous_mixing.adapters.rendering.wav_output import _io_failed as _io_failed
from autonomous_mixing.adapters.rendering.wav_output import _open_exclusive as _open_exclusive
from autonomous_mixing.adapters.rendering.wav_output import _payload_problem as _payload_problem
from autonomous_mixing.adapters.rendering.wav_output import _prepare as _prepare
from autonomous_mixing.adapters.rendering.wav_output import _read_back as _read_back
from autonomous_mixing.adapters.rendering.wav_output import _require_count as _require_count
from autonomous_mixing.adapters.rendering.wav_output import _require_instance as _require_instance
from autonomous_mixing.adapters.rendering.wav_output import _require_samples as _require_samples
from autonomous_mixing.adapters.rendering.wav_output import _require_text as _require_text
from autonomous_mixing.adapters.rendering.wav_output import _riff_size_problem as _riff_size_problem
from autonomous_mixing.adapters.rendering.wav_output import _snapshot as _snapshot
from autonomous_mixing.adapters.rendering.wav_output import _validate_arguments as _validate_arguments
from autonomous_mixing.adapters.rendering.wav_output import _verify_captured as _verify_captured
from autonomous_mixing.adapters.rendering.wav_output import _write_all as _write_all

__all__ = ["WavOutputFailureCode", "WavOutputError", "WrittenWav", "write_wav_output"]
