"""Full-band sample-peak and pooled-channel RMS level kernels, in dBFS (file-audio-analyzer design).

Both kernels take one finite float64 ``(frames, channels)`` array with one or two channels and leave it
unchanged. They describe the selected samples only: no resampling, padding, channel folding, weighting,
normalization or clipping is applied, and values above full scale (possible in float WAV files) give
positive dBFS without a cap.

- Peak: ``20 * log10(max |x|)`` over all frames and channels.
- RMS: square root of the mean square over all frames and channels (pooled, not a mono fold-down, so
  antiphase stereo does not cancel), computed through peak scaling as
  ``20 * log10(p) + 10 * log10(mean((x / p) ** 2))`` for peak ``p > 0``. Scaled values lie in [-1, 1], so
  no large raw square overflows and the mean is at least ``1 / n``; the mean uses NumPy's float64
  ``np.mean`` reduction (pairwise summation).

Exact digital silence (peak zero) is ``Unavailable("silent_signal")`` because a scalar record cannot hold
minus infinity; no epsilon or finite floor is used. An empty selection is
``Unavailable("empty_time_selection")``. Results are float64 computations: agreement with analytical
values holds to ordinary floating-point rounding, not bitwise across platforms. The M0 ``features.rms_dbfs``
(direct squaring) is a separate, unversioned descriptive helper and is not reused here.
"""

import numpy as np

from autonomous_mixing.core.records.observation import MeasuredScalar, Unavailable

DBFS = "dBFS"
SILENT_SIGNAL = "silent_signal"
EMPTY_TIME_SELECTION = "empty_time_selection"

LevelResult = MeasuredScalar | Unavailable


def _require_samples(samples: object) -> np.ndarray:
    if not isinstance(samples, np.ndarray):
        raise ValueError(f"samples must be a NumPy array, got {type(samples).__name__}")
    if samples.dtype != np.float64:
        raise ValueError(f"samples must have dtype float64, got {samples.dtype}")
    if samples.ndim != 2 or samples.shape[1] not in (1, 2):
        raise ValueError(f"samples must have shape (frames, 1 or 2 channels), got {samples.shape}")
    if not np.isfinite(samples).all():
        raise ValueError("samples must be finite")
    return samples


def _peak(samples: np.ndarray) -> float:
    return float(np.max(np.abs(samples)))


def sample_peak_dbfs(samples: np.ndarray) -> LevelResult:
    """Full-band sample peak over all frames and channels, in dBFS."""
    samples = _require_samples(samples)
    if samples.shape[0] == 0:
        return Unavailable(EMPTY_TIME_SELECTION)
    peak = _peak(samples)
    if peak == 0.0:
        return Unavailable(SILENT_SIGNAL)
    return MeasuredScalar(20.0 * float(np.log10(peak)), DBFS)


def sample_rms_dbfs(samples: np.ndarray) -> LevelResult:
    """Unweighted pooled-channel RMS over all frames and channels, in dBFS, computed through peak scaling."""
    samples = _require_samples(samples)
    if samples.shape[0] == 0:
        return Unavailable(EMPTY_TIME_SELECTION)
    peak = _peak(samples)
    if peak == 0.0:
        return Unavailable(SILENT_SIGNAL)
    # Underflow of negligible terms to zero is expected and harmless: the largest scaled magnitude is exactly 1,
    # so the mean square stays >= 1 / n > 0. It is ignored locally, whatever the caller's NumPy error policy.
    with np.errstate(under="ignore"):
        scaled = samples / peak
        squares = np.square(scaled)
    mean_square = float(np.mean(squares, dtype=np.float64))
    return MeasuredScalar(20.0 * float(np.log10(peak)) + 10.0 * float(np.log10(mean_square)), DBFS)
