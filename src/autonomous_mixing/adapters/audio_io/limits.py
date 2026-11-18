"""Read limits for one captured WAV file and its decoded samples (ADR-028).

``max_file_bytes`` bounds the captured payload and ``max_decoded_bytes`` the returned float64 sample
storage of one file. Both are required, exact built-in ints of at least one, with no caps, defaults or
conversion; they are resource bounds, not scientific thresholds. Nothing here touches the filesystem.
``_require_limit`` is the WAV reader's existing check, shared within this component only.
"""

from dataclasses import dataclass
from typing import Any


def _require_limit(value: Any, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive built-in int, got {value!r}")
    return value


@dataclass(frozen=True)
class AudioReadLimits:
    """Validated per-file read limits; the file limit is checked before the decoded limit."""

    max_file_bytes: int
    max_decoded_bytes: int

    def __post_init__(self) -> None:
        _require_limit(self.max_file_bytes, "max_file_bytes")
        _require_limit(self.max_decoded_bytes, "max_decoded_bytes")
