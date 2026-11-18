"""Spreading and tonality input of the dist10 front end (front-end contract §3.4; Task 3J).

Profile v1 (44.1 kHz). Following ISO dist10 ``L3para_read`` (l3psy.c, original ll. 819-838) and ``L3psycho_anal``
(ll. 573-604):

Matrix ``s3[i][j]``, row i = output (centre) partition, column j = input (masker) partition, in float64::

    tempx = (bval[i] - bval[j]) * (3.0 if j >= i else 1.5)     # steeper towards lower output partitions
    x     = 8 (t^2 - 2 t), t = tempx - 0.5, if 0.5 <= tempx <= 2.5, else 0
    tempx = tempx + 0.474
    tempy = 15.811389 + 7.5 tempx - 17.5 sqrt(1 + tempx^2)
    s3    = 0 if tempy <= -60 (tested before x is added), else exp((x + tempy) * 0.2302585093)

with dist10's truncated ``LN_TO_LOG10`` (common.h l. 199) and ``bval`` from the pinned ISO dist10 ``tables/psy_data``
(SHA-256 ``9094946e8cc36a30d956571ab3731e57af6e0ce7127a0d33edf11d91a3ef0c48``), block ``freq = 44100.0 partition = 62``,
field ``bval`` of No=0..62 (embedded below as the decimal values written there, so the kernel runs without fixtures).
Python's ``math.exp``/``math.sqrt`` may differ from the reference's libm in the last bit; against Probe F the matrix
agrees within 1e-15 relative with the same zero pattern.

Per call: for output b = 0..62 and input k = 0..62 ascending, ``ecb[b] += s3[b][k] * eb[k]`` (float64 product and
sum, **rounded to float32 after every term**: ``ecb`` is a FLOAT work array) and ``ctb[b] += s3[b][k] * cb[k]``
(float64). Zero matrix entries add exactly zero and are skipped. Then, only where ``ecb[b] != 0``: ``ratio = ctb/ecb``
(float64), clamped below at 0.01 (no upper clamp), and ``log_ratio = log(clamped ratio)``; where the division is
skipped, ``ratio`` and ``log_ratio`` are 0.0 (the reference passes 0.0 to the tonality step). Floored spectra make
every ``eb`` positive and ``s3[b][b]`` is about 1, so the skip is reached only by synthetic zero-energy input.

No tonality (``tbb``), SNR, threshold history or band mapping here.
"""

import math
from dataclasses import dataclass

import numpy as np

PARTITIONS = 63
LN_TO_LOG10 = 0.2302585093  # dist10 common.h (truncated ln(10) / 10)
RATIO_FLOOR = 0.01
# tables/psy_data, "freq = 44100.0 partition = 62", No=0..62, field bval (Bark)
DIST10_LONG_BVAL_44100 = (
    0.000, 0.431, 0.861, 1.292, 1.723, 2.153, 2.584, 3.015, 3.445, 3.876, 4.279, 4.670, 5.057, 5.416, 5.774,
    6.422, 7.026, 7.609, 8.168, 8.710, 9.207, 9.662, 10.099, 10.515, 10.917, 11.293, 11.652, 11.997, 12.394,
    12.850, 13.277, 13.681, 14.062, 14.411, 14.751, 15.119, 15.508, 15.883, 16.263, 16.654, 17.020, 17.374,
    17.744, 18.104, 18.447, 18.782, 19.130, 19.487, 19.838, 20.179, 20.510, 20.852, 21.196, 21.531, 21.870,
    22.214, 22.558, 22.898, 23.232, 23.564, 23.897, 24.229, 24.442,
)  # fmt: skip
_CHUNK_CALLS = 1024


def _build_matrix(bval: tuple[float, ...]) -> np.ndarray:
    n = len(bval)
    s3 = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(n):
            tempx = (bval[i] - bval[j]) * (3.0 if j >= i else 1.5)
            if 0.5 <= tempx <= 2.5:
                t = tempx - 0.5
                x = 8.0 * (t * t - 2.0 * t)
            else:
                x = 0.0
            tempx += 0.474
            tempy = 15.811389 + 7.5 * tempx - 17.5 * math.sqrt(1.0 + tempx * tempx)
            s3[i, j] = 0.0 if tempy <= -60.0 else math.exp((x + tempy) * LN_TO_LOG10)
    s3.flags.writeable = False
    return s3


_MATRIX = _build_matrix(DIST10_LONG_BVAL_44100)


@dataclass(frozen=True, eq=False)
class SpreadTonalityInput:
    """Per-call spread values and tonality input (adapter-local, not a core record), each ``(calls, 63)`` by output
    partition: ``ecb`` float32, ``ctb`` float64, ``divided`` bool (the division ran), ``ratio`` float64 (``ctb/ecb``,
    0.0 where not divided, unclamped) and ``log_ratio`` float64 (log of the clamped ratio, 0.0 where not divided).
    All arrays are freshly allocated and read-only. Equality is identity."""

    ecb: np.ndarray
    ctb: np.ndarray
    divided: np.ndarray
    ratio: np.ndarray
    log_ratio: np.ndarray


def dist10_spreading_matrix() -> np.ndarray:
    """The 44.1 kHz long spreading matrix ``s3[output][input]``, float64 ``(63, 63)``; a fresh read-only copy."""
    copy = _MATRIX.copy()
    copy.flags.writeable = False
    return copy


def _require(values: object, name: str, dtype: type, shape_tail: tuple[int, ...]) -> np.ndarray:
    if not isinstance(values, np.ndarray) or isinstance(values, np.ma.MaskedArray):
        raise ValueError(f"{name} must be a plain NumPy array")
    values = np.asarray(values)
    if values.dtype != dtype:
        raise ValueError(f"{name} must have dtype {np.dtype(dtype)}, got {values.dtype}")
    if values.ndim != 1 + len(shape_tail) or values.shape[1:] != shape_tail:
        raise ValueError(f"{name} must have shape (n, {', '.join(map(str, shape_tail))}), got {values.shape}")
    for start in range(0, values.shape[0], _CHUNK_CALLS):  # chunked, so validation temporaries stay bounded
        block = values[start : start + _CHUNK_CALLS]
        if not np.isfinite(block).all():
            raise ValueError(f"{name} must be finite")
        if (block < 0).any():
            raise ValueError(f"{name} must be non-negative")
    return values


def _reject_inf(values: np.ndarray, label: str, start: int) -> None:
    overflowed = np.isinf(values)
    if overflowed.any():
        row, partition = np.argwhere(overflowed)[0]
        raise ValueError(f"{label} range at call {start + int(row)}, output partition {int(partition)}")


def dist10_spread_tonality_input(
    eb: np.ndarray, cb: np.ndarray, *, spreading_matrix: np.ndarray | None = None
) -> SpreadTonalityInput:
    """Spread ``eb`` (float64) and ``cb`` (float32), each ``(calls, 63)``, and form the tonality input per partition.

    ``spreading_matrix`` (float64 ``(63, 63)``, row = output) defaults to the dist10 44.1 kHz matrix; supplying one is
    for reference comparisons. Raises ``ValueError`` for non-ndarray or masked inputs, other dtypes or shapes,
    mismatched call counts, non-finite or negative values, an ``ecb`` beyond the float32 range, or a ``ctb`` or
    ``ctb/ecb`` beyond the float64 range (the last two need a supplied matrix with large entries). The first call and
    output partition are named, and the caller's NumPy error policy does not change the outcome. Inputs are never
    modified.
    """
    e = _require(eb, "eb", np.float64, (PARTITIONS,))
    c = _require(cb, "cb", np.float32, (PARTITIONS,))
    if e.shape[0] != c.shape[0]:
        raise ValueError(f"eb and cb must have the same number of calls, got {e.shape[0]} and {c.shape[0]}")
    s3 = _MATRIX if spreading_matrix is None else _require(spreading_matrix, "spreading_matrix", np.float64, (63,))
    if s3.shape != (PARTITIONS, PARTITIONS):
        raise ValueError(f"spreading_matrix must have shape (63, 63), got {s3.shape}")
    terms = [[(k, float(s3[b, k])) for k in range(PARTITIONS) if s3[b, k] != 0.0] for b in range(PARTITIONS)]
    calls = e.shape[0]
    shape = (calls, PARTITIONS)
    ecb = np.zeros(shape, dtype=np.float32)
    ctb = np.zeros(shape, dtype=np.float64)
    divided = np.zeros(shape, dtype=np.bool_)
    ratio = np.zeros(shape, dtype=np.float64)
    log_ratio = np.zeros(shape, dtype=np.float64)
    for start in range(0, calls, _CHUNK_CALLS):
        rows = slice(start, min(start + _CHUNK_CALLS, calls))
        e_rows = e[rows]
        c_rows = c[rows].astype(np.float64)
        ecb_rows = np.zeros((e_rows.shape[0], PARTITIONS), dtype=np.float32)
        ctb_rows = np.zeros((e_rows.shape[0], PARTITIONS), dtype=np.float64)
        # The caller's NumPy error policy must not change the result: rounding a tiny term to a float32 subnormal or
        # zero is the reference's float32 store (underflow), and an out-of-range value becomes inf and is rejected
        # below (overflow). With the dist10 matrix (entries <= 1) and valid partition sums, ctb and the ratio stay
        # finite (ctb <= 63 x float32 max, ratio <= that over the smallest nonzero float32 ecb); a supplied matrix
        # with large entries can exceed float64.
        with np.errstate(over="ignore", under="ignore"):
            for b in range(PARTITIONS):
                for k, weight in terms[b]:  # ascending input partition
                    ecb_rows[:, b] = ecb_rows[:, b].astype(np.float64) + weight * e_rows[:, k]  # float32 store
                    ctb_rows[:, b] += weight * c_rows[:, k]
        # Accumulators are checked before dividing, so an inf / inf (invalid) or inf / x division is never reached.
        _reject_inf(ecb_rows, "ecb exceeds the float32", start)
        _reject_inf(ctb_rows, "ctb exceeds the float64", start)
        div = ecb_rows != 0.0
        rat = np.zeros_like(ctb_rows)
        with np.errstate(over="ignore", under="ignore"):  # finite operands; an overflowing quotient is rejected next
            np.divide(ctb_rows, ecb_rows.astype(np.float64), out=rat, where=div)
        _reject_inf(rat, "ctb/ecb exceeds the float64", start)
        log_rows = np.zeros_like(ctb_rows)
        np.log(np.maximum(rat, RATIO_FLOOR), out=log_rows, where=div)  # lower clamp before the log
        ecb[rows], ctb[rows], divided[rows], ratio[rows], log_ratio[rows] = ecb_rows, ctb_rows, div, rat, log_rows
    for array in (ecb, ctb, divided, ratio, log_ratio):
        array.flags.writeable = False
    return SpreadTonalityInput(ecb=ecb, ctb=ctb, divided=divided, ratio=ratio, log_ratio=log_ratio)
