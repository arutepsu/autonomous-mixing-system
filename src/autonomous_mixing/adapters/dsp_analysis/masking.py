"""Directional masking margin on a common frame-by-band grid (Ronan 2018, thesis Ch. 7, Eq. 7.12).

For an ordered pair A -> B, A supplies the masking threshold ``T`` and B the target energy ``E`` on the same
frames and bands. Each strictly positive cell gets the margin ``10 log10(T) - 10 log10(E)`` in dB: positive is
an exceedance of the target energy by the threshold under the model, equality is exactly zero. One call is
one direction; B -> A needs B's threshold and A's energy. The kernel does not compute thresholds, read audio,
aggregate (Eq. 7.13), diagnose or establish perceptual masking; the caller supplies the common grid and
compatible units, and only the shape is checked here. Model exceedance is an uncalibrated signal.

Zero cells (project decision): ``zero_threshold`` and ``zero_target_energy`` mark exact zeros independently,
so both are true when both inputs are zero, and every marked cell has a NaN margin. A zero-energy target
supplies no audibility evidence and a zero threshold is kept distinct from it; no finite floor is used.

Numerics: only positive cells are evaluated, so the caller's NumPy error settings (even "raise") are never
triggered by zero cells. Widely separated values use the difference of separate base-10 logarithms, because
the ratio itself may overflow or underflow. Cells whose log10 values differ by less than 0.25 use
``log1p((T - E) / E)`` converted to base 10, which avoids cancellation; there ``T - E`` is exact (the values
lie within a factor of two) and the quotient neither overflows nor underflows. The 0.25 switch only selects
the evaluation formula; it is not a scientific threshold. Exact equality gives +0.0.
"""

import math
from dataclasses import dataclass

import numpy as np

# Selects the cancellation-free formula for nearly equal values; any value below log10(2) keeps T - E exact.
_NEAR_LOG10_DIFFERENCE = 0.25
_DB_PER_NEPER = 10.0 / math.log(10.0)


@dataclass(frozen=True, eq=False)
class MaskingMarginGrid:
    """Signed margins in dB and exact-zero masks, all shaped like the inputs (adapter-local, not a core record).

    ``margin_db`` is float64 with NaN wherever either mask is true; the masks are boolean. All three arrays are
    freshly allocated and returned read-only. Equality is identity (arrays have no single truth value).
    """

    margin_db: np.ndarray
    zero_threshold: np.ndarray
    zero_target_energy: np.ndarray


def _require_grid(values: object, name: str) -> np.ndarray:
    if not isinstance(values, np.ndarray):
        raise ValueError(f"{name} must be a NumPy array, got {type(values).__name__}")
    if isinstance(values, np.ma.MaskedArray):
        # A mask can hide invalid stored values from reductions; masked cells have no defined margin here.
        raise ValueError(f"{name} must not be a masked array")
    # Other subclasses (e.g. np.matrix) are viewed as plain ndarrays so that their operator overrides cannot
    # change validation or arithmetic; asarray keeps read-only flags and strides and copies nothing.
    values = np.asarray(values)
    if values.dtype != np.float64:
        raise ValueError(f"{name} must have dtype float64, got {values.dtype}")
    if values.ndim != 2 or values.shape[1] < 1:
        raise ValueError(f"{name} must have shape (frames, bands) with at least one band, got {values.shape}")
    if not np.isfinite(values).all():
        raise ValueError(f"{name} must be finite")
    if (values < 0).any():
        raise ValueError(f"{name} must be non-negative")
    return values


def directional_masking_margin_db(masker_threshold: np.ndarray, target_energy: np.ndarray) -> MaskingMarginGrid:
    """Margin ``10 log10(T / E)`` per frame and band of one masker threshold over one target's energy.

    Masked arrays are rejected with ``ValueError``; other ndarray subclasses are viewed as plain ndarrays
    before validation, and the results are always plain ndarrays.
    """
    threshold = _require_grid(masker_threshold, "masker_threshold")
    energy = _require_grid(target_energy, "target_energy")
    if threshold.shape != energy.shape:
        raise ValueError(f"shape mismatch: masker_threshold {threshold.shape}, target_energy {energy.shape}")
    zero_threshold = threshold == 0.0
    zero_target_energy = energy == 0.0
    positive = ~(zero_threshold | zero_target_energy)
    margin = np.full(threshold.shape, np.nan, dtype=np.float64)
    t, e = threshold[positive], energy[positive]  # copies of the positive cells only
    log_difference = np.log10(t) - np.log10(e)
    near = np.abs(log_difference) < _NEAR_LOG10_DIFFERENCE
    values = 10.0 * log_difference
    values[near] = _DB_PER_NEPER * np.log1p((t[near] - e[near]) / e[near])
    margin[positive] = values
    for array in (margin, zero_threshold, zero_target_energy):
        array.setflags(write=False)
    return MaskingMarginGrid(margin, zero_threshold, zero_target_energy)
