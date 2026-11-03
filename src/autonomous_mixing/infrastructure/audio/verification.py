"""Compatibility shim (ADR-027 migration step M3): definitions moved to ``autonomous_mixing.adapters.rendering.integrity``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.adapters.rendering.integrity import _CHANNEL_COUNTS as _CHANNEL_COUNTS
from autonomous_mixing.adapters.rendering.integrity import _FLOAT64_BYTES as _FLOAT64_BYTES
from autonomous_mixing.adapters.rendering.integrity import _READ_CHUNK_BYTES as _READ_CHUNK_BYTES
from autonomous_mixing.adapters.rendering.integrity import _SEPARATORS as _SEPARATORS
from autonomous_mixing.adapters.rendering.integrity import _URI_SCHEME as _URI_SCHEME
from autonomous_mixing.adapters.rendering.integrity import (
    WAV_CONTAINERS,
    ArtifactVerificationError,
    VerificationFailureCode,
    VerifiedAudio,
    verify_resolved_wav_artifact,
    verify_wav_artifact,
)
from autonomous_mixing.adapters.rendering.integrity import _capture as _capture
from autonomous_mixing.adapters.rendering.integrity import _decode as _decode
from autonomous_mixing.adapters.rendering.integrity import _fail as _fail
from autonomous_mixing.adapters.rendering.integrity import _local_absolute_path as _local_absolute_path
from autonomous_mixing.adapters.rendering.integrity import _require_limit as _require_limit
from autonomous_mixing.adapters.rendering.integrity import _require_regular as _require_regular
from autonomous_mixing.adapters.rendering.integrity import _verify_at as _verify_at

__all__ = [
    "WAV_CONTAINERS",
    "VerificationFailureCode",
    "ArtifactVerificationError",
    "VerifiedAudio",
    "verify_wav_artifact",
    "verify_resolved_wav_artifact",
]
