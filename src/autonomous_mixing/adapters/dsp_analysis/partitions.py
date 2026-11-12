"""Long partition accumulator of the dist10 front end (front-end contract §3.3; Task 3H).

Profile v1 (44.1 kHz). For each call, following ISO dist10 ``L3psycho_anal`` (l3psy.c, original ll. 553-565):

    eb[b] = cb[b] = 0 for b = 0..62
    for j = 0..512:  b = partition_l[j];  eb[b] += energy[j];  cb[b] += cw[j] * energy[j]

``eb`` is double in the reference: float32 energies accumulated in float64, bins in ascending order. ``cb`` is a
float32 (``FLOAT``) work array: each product and sum is formed in float64 and the result rounded to float32 after
every bin, so the order of the bins changes the result and is kept.

Mapping: ``partition_l`` comes from the pinned ISO dist10 ``tables/psy_data`` (SHA-256 ``9094946e8cc36a30d956571ab37
31e57af6e0ce7127a0d33edf11d91a3ef0c48``), block ``freq = 44100.0 partition = 62``: the 63 long partitions' ``#lines``
below, assigned to bins in order (465 bins). Bins 465-512 are not covered by the table and keep the reference's
static zero initialisation, so they accumulate into partition 0 together with DC (49 bins, Nyquist included). The
table is embedded here so the kernel runs without fixtures; the tests check it against the Probe E capture.

No spreading, tonality or threshold here.
"""

from dataclasses import dataclass

import numpy as np

LONG_BINS = 513  # HBLKSIZE
LONG_PARTITIONS = 63  # CBANDS for the 44.1 kHz long table
# tables/psy_data, "freq = 44100.0 partition = 62", No=0..62, field #lines
DIST10_LONG_PARTITION_LINES_44100 = (
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2,
    3, 3, 3, 3, 3, 3, 3,
    4, 4, 4,
    5, 5, 5,
    6, 6, 7, 7, 7, 9, 9, 9, 10, 10, 11, 14, 14, 15, 20, 20, 21, 27, 27, 36, 73, 18,
)  # fmt: skip
_CHUNK_CALLS = 256


def _build_map() -> np.ndarray:
    lines = np.array(DIST10_LONG_PARTITION_LINES_44100)
    assert lines.size == LONG_PARTITIONS and lines.sum() <= LONG_BINS
    mapping = np.zeros(LONG_BINS, dtype=np.intp)  # unassigned bins stay 0, as the static array does
    mapping[: lines.sum()] = np.repeat(np.arange(LONG_PARTITIONS), lines)
    mapping.flags.writeable = False
    return mapping


_MAP = _build_map()


@dataclass(frozen=True, eq=False)
class PartitionSums:
    """Per-call partition sums (adapter-local, not a core record): ``eb`` float64 and ``cb`` float32, each
    ``(calls, 63)``, rows in call order. Both arrays are freshly allocated and read-only. Equality is identity."""

    eb: np.ndarray
    cb: np.ndarray


def dist10_long_partition_map() -> np.ndarray:
    """The 44.1 kHz bin-to-partition map ``partition_l`` (shape ``(513,)``); a fresh read-only copy on each call."""
    copy = _MAP.copy()
    copy.flags.writeable = False
    return copy


def _require(values: object, name: str, dtype: type) -> np.ndarray:
    if not isinstance(values, np.ndarray) or isinstance(values, np.ma.MaskedArray):
        raise ValueError(f"{name} must be a plain NumPy array")
    values = np.asarray(values)
    if values.dtype != dtype:
        raise ValueError(f"{name} must have dtype {np.dtype(dtype)}, got {values.dtype}")
    if values.ndim != 2 or values.shape[1] != LONG_BINS:
        raise ValueError(f"{name} must have shape (calls, {LONG_BINS}), got {values.shape}")
    for start in range(0, values.shape[0], _CHUNK_CALLS):  # chunked, so validation temporaries stay bounded
        block = values[start : start + _CHUNK_CALLS]
        if not np.isfinite(block).all():
            raise ValueError(f"{name} must be finite")
        if (block < 0).any():
            raise ValueError(f"{name} must be non-negative")
    return values


def dist10_partition_sums(energy: np.ndarray, cw: np.ndarray) -> PartitionSums:
    """Accumulate ``eb`` and ``cb`` for aligned float32 post-floor long energies and float64 ``cw``, ``(calls, 513)``.

    Raises ``ValueError`` for non-ndarray or masked inputs, other dtypes or shapes, mismatched call counts,
    non-finite or negative values, or finite inputs whose ``cb`` sum would exceed the float32 range (the first
    affected call and partition are named; the caller's NumPy error policy does not change this). Inputs are never
    modified; zero calls give empty results.
    """
    e = _require(energy, "energy", np.float32)
    c = _require(cw, "cw", np.float64)
    if e.shape[0] != c.shape[0]:
        raise ValueError(f"energy and cw must have the same number of calls, got {e.shape[0]} and {c.shape[0]}")
    calls = e.shape[0]
    eb = np.zeros((calls, LONG_PARTITIONS), dtype=np.float64)
    cb = np.zeros((calls, LONG_PARTITIONS), dtype=np.float32)
    for start in range(0, calls, _CHUNK_CALLS):
        rows = slice(start, min(start + _CHUNK_CALLS, calls))
        e64 = e[rows].astype(np.float64)  # float32 -> float64 is exact
        c64 = c[rows]
        eb_rows = np.zeros((e64.shape[0], LONG_PARTITIONS), dtype=np.float64)
        cb_rows = np.zeros((e64.shape[0], LONG_PARTITIONS), dtype=np.float32)
        # Overflow is contained locally and then rejected: a cb sum beyond the float32 range rounds to inf in the
        # float32 store (and a float64 product overflow gives inf), and inf stays inf for the rest of the chunk,
        # because every term is finite and non-negative. eb cannot overflow: 513 x float32 max is far below the
        # float64 range.
        with np.errstate(over="ignore"):
            for j in range(LONG_BINS):  # ascending bin order, vectorised over the calls of the chunk
                b = _MAP[j]
                eb_rows[:, b] += e64[:, j]
                cb_rows[:, b] = cb_rows[:, b].astype(np.float64) + c64[:, j] * e64[:, j]  # float32 store per bin
        overflowed = np.isinf(cb_rows)
        if overflowed.any():
            row, partition = np.argwhere(overflowed)[0]
            raise ValueError(
                f"cb exceeds the float32 range at call {start + int(row)}, partition {int(partition)}; "
                "the energies and cw are outside the range the reference can represent"
            )
        eb[rows], cb[rows] = eb_rows, cb_rows
    eb.flags.writeable = False
    cb.flags.writeable = False
    return PartitionSums(eb=eb, cb=cb)
