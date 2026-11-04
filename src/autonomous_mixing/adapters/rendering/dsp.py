"""Fixed-input absolute gain and recipe-ordered stem summation on in-memory float64 sample arrays.

These kernels implement the numerical rules of ``ProcessingSpec`` (FLOAT64, DB_AMPLITUDE,
RECIPE_SEQUENTIAL, PRESERVE); its container and sample-encoding settings belong to a later file
adapter. They contribute to FR-08, FR-17, QR-01 and QR-02 but perform no I/O, rendering or
verification, and make no perceptual claim. Sample arrays are ordinary native-endian float64
``(frames, channels)`` arrays with at least one frame, one or two channels and finite samples; they
are never cast, reshaped, aligned or mutated, and results are always newly allocated. Floating-point
conditions are handled locally, leaving the caller's NumPy error settings untouched; rejected inputs
and unrepresentable results raise ValueError. A fixed operation order does not promise
bitwise-identical results on every platform.
"""

import math
from typing import Any

import numpy as np

_CHANNEL_COUNTS = (1, 2)


def _require_samples(value: Any, field: str) -> np.ndarray:
    if type(value) is not np.ndarray:
        raise ValueError(f"{field} must be a numpy.ndarray (not a subclass), got {type(value).__name__}")
    if value.dtype.type is not np.float64 or not value.dtype.isnative:
        raise ValueError(f"{field} must have native-endian float64 dtype, got {value.dtype.str}")
    if value.ndim != 2:
        raise ValueError(f"{field} must have shape (frames, channels), got {value.shape}")
    frames, channels = value.shape
    if frames < 1:
        raise ValueError(f"{field} must contain at least one frame")
    if channels not in _CHANNEL_COUNTS:
        raise ValueError(f"{field} must have 1 or 2 channels, got {channels}")
    if not np.isfinite(value).all():
        raise ValueError(f"{field} must contain only finite samples")
    return value


def _gain_factor(gain_db: Any) -> float:
    """Return 10 ** (gain_db / 20) as a finite, strictly positive float64 (subnormal allowed)."""
    # Exact built-in int/float, checked before any conversion or arithmetic so that no subclass
    # override runs; this rejects bool, every NumPy scalar (numpy.float64 included) and subclasses.
    if type(gain_db) is not int and type(gain_db) is not float:
        raise ValueError(f"gain_db must be a built-in int or float, got {type(gain_db).__name__}")
    try:
        factor = 10 ** (gain_db / 20)
    except OverflowError:
        raise ValueError(f"gain_db {gain_db!r} gives a gain factor outside the float64 range") from None
    if not math.isfinite(factor):
        raise ValueError(f"gain_db {gain_db!r} gives a non-finite gain factor")
    if factor <= 0.0:
        raise ValueError(f"gain_db {gain_db!r} gives a gain factor that rounds to zero")
    return factor


def apply_absolute_gain(input_samples: np.ndarray, gain_db: float) -> np.ndarray:
    """Return the fixed input multiplied by 10 ** (gain_db / 20); repeated calls never compound.

    Finite values beyond full scale are kept; products that underflow to subnormal or zero are kept.
    """
    samples = _require_samples(input_samples, "input_samples")
    factor = _gain_factor(gain_db)
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        result = np.multiply(samples, factor)
    if not np.isfinite(result).all():
        raise ValueError(f"applying gain_db {gain_db!r} overflows the float64 range")
    return result


def sum_stems(stems: tuple[np.ndarray, ...]) -> np.ndarray:
    """Return a fresh zero accumulator plus each stem, added one at a time in tuple order.

    Every intermediate sum must stay finite, even if later stems would cancel an overflow. No
    reordering, compensation, normalization, clipping or limiting is applied.
    """
    if type(stems) is not tuple or not stems:
        raise ValueError("stems must be a nonempty tuple of sample arrays")
    arrays = tuple(_require_samples(stem, f"stems[{index}]") for index, stem in enumerate(stems))
    shape = arrays[0].shape
    for index, array in enumerate(arrays):
        if array.shape != shape:
            raise ValueError(f"stems[{index}] has shape {array.shape}, expected {shape}")
    accumulator = np.zeros(shape, dtype=np.float64)
    for index, array in enumerate(arrays):
        with np.errstate(over="ignore", under="ignore", invalid="ignore"):
            np.add(accumulator, array, out=accumulator)
        if not np.isfinite(accumulator).all():
            raise ValueError(f"adding stems[{index}] overflows the float64 range")
    return accumulator
