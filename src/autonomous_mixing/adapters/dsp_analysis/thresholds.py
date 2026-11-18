"""Partition thresholds of the dist10 front end (front-end contract §3.5; Task 3L).

Profile v1 (44.1 kHz, one mono clip per invocation). Following ISO dist10 ``L3psycho_anal`` (l3psy.c, original
ll. 602-616), per call k in order and partition b = 0..62, from the spread energy ``ecb`` (float32) and the tonality
input ``cbb`` (log of the clamped ``ctb/ecb``, or 0.0 where the division was skipped; see ``spreading``)::

    tbb     = -0.299 - 0.43 cbb                                   # double
    tbb     = minimum(1.0, maximum(0.0, tbb))
    SNR     = maximum(minval[b], 29 tbb + 6 (1 - tbb))            # dB; TMN = 29, NMT = 6
    nb      = float32( (ecb norm[b]) exp(-SNR 0.2302585093) )     # FLOAT work array: stored as float32
    temp_1  = minimum(nb, minimum(2 nb_1[b], 16 nb_2[b]))         # pre-echo limit, double
    thr     = maximum(qthr[b], temp_1)                            # threshold in quiet applied after the limit
    nb_2[b] = nb_1[b];  nb_1[b] = nb                              # history stores the unbounded nb, not thr

with ``maximum(x, y) = x > y ? x : y`` and ``minimum(x, y) = x < y ? x : y`` (l3psy.c ll. 33-34) and dist10's
truncated ``LN_TO_LOG10`` (common.h l. 199). ``nb_1`` and ``nb_2`` start at zero for each clip (static zero
initialisation, l. 75), so calls 0 and 1 give ``thr = qthr``. Every call is processed, including start-up calls and
calls on which the encoder would take the short-block path: dist10 updates the history before that branch. Since
``nb`` does not depend on the history, the limit is formed from ``nb`` shifted by one and two calls; the last two
``nb`` rows carry across chunks.

Tables: ``minval``, ``qthr`` and ``norm`` of the pinned ISO dist10 ``tables/psy_data`` (SHA-256
``9094946e8cc36a30d956571ab3731e57af6e0ce7127a0d33edf11d91a3ef0c48``), block ``freq = 44100.0 partition = 62``, rows
No=0..62, embedded as the decimal values written there (``L3para_read`` parses them with ``%lf``, l. 784). Against
Probe G (14 calls replayed from call 0 with the captured spread inputs) every output is exact, ``nb`` included.

No band mapping (``en``/``thm``), no short-block thresholds and no perceptual entropy here.
"""

from dataclasses import dataclass

import numpy as np

PARTITIONS = 63
LN_TO_LOG10 = 0.2302585093  # dist10 common.h (truncated ln(10) / 10)
TONALITY_OFFSET, TONALITY_SLOPE = -0.299, -0.43  # conv1, conv2 (l. 602)
TONE_MASKING_NOISE_DB, NOISE_MASKING_TONE_DB = 29.0, 6.0  # TMN, NMT (l. 604)
PRE_ECHO_PREVIOUS, PRE_ECHO_SECOND_PREVIOUS = 2.0, 16.0  # rpelev, rpelev2 (l. 613)
# tables/psy_data, "freq = 44100.0 partition = 62", No=0..62: fields minval (dB), qthr (energy) and norm
DIST10_LONG_MINVAL_44100 = (
    24.5, 24.5, 24.5, 24.5, 24.5, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 18.0, 18.0, 18.0, 12.0, 6.0, 6.0, 3.0,
    3.0, 3.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
)  # fmt: skip
DIST10_LONG_QTHR_44100 = (
    4.532, 4.532, 4.532, 0.904, 0.904, 0.090, 0.090, 0.029, 0.029, 0.029, 0.009, 0.009, 0.009, 0.009, 0.009, 0.018,
    0.018, 0.018, 0.018, 0.018, 0.018, 0.018, 0.018, 0.018, 0.018, 0.018, 0.018, 0.018, 0.027, 0.027, 0.027, 0.027,
    0.027, 0.027, 0.027, 0.036, 0.036, 0.036, 0.045, 0.045, 0.045, 0.054, 0.054, 0.063, 0.063, 0.063, 0.081, 0.081,
    0.081, 0.180, 0.180, 0.198, 0.400, 0.400, 0.429, 1.808, 1.808, 1.898, 24.416, 24.416, 32.554, 660.124, 162.770,
)  # fmt: skip
DIST10_LONG_NORM_44100 = (
    0.951, 0.700, 0.681, 0.675, 0.667, 0.665, 0.664, 0.664, 0.664, 0.655, 0.616, 0.597, 0.578, 0.541, 0.575, 0.856,
    0.846, 0.840, 0.822, 0.800, 0.753, 0.704, 0.674, 0.640, 0.609, 0.566, 0.535, 0.531, 0.615, 0.686, 0.650, 0.612,
    0.567, 0.520, 0.513, 0.557, 0.584, 0.570, 0.579, 0.585, 0.548, 0.536, 0.550, 0.532, 0.504, 0.496, 0.517, 0.527,
    0.516, 0.497, 0.489, 0.502, 0.501, 0.491, 0.497, 0.504, 0.504, 0.495, 0.486, 0.484, 0.483, 0.475, 0.515,
)  # fmt: skip
_MINVAL = np.array(DIST10_LONG_MINVAL_44100, dtype=np.float64)
_QTHR = np.array(DIST10_LONG_QTHR_44100, dtype=np.float64)
_NORM = np.array(DIST10_LONG_NORM_44100, dtype=np.float64)
for _table in (_MINVAL, _QTHR, _NORM):
    _table.flags.writeable = False
_CHUNK_CALLS = 1024


@dataclass(frozen=True, eq=False)
class PartitionThresholds:
    """Per-call partition thresholds (adapter-local, not a core record), each ``(calls, 63)``: ``tonality`` float64
    (clamped tbb), ``snr`` float64 (dB), ``nb`` float32 (unbounded threshold, the value kept as history),
    ``pre_echo_limit`` float64 (temp_1) and ``thr`` float64 (final threshold before band mapping). All arrays are
    freshly allocated and read-only. Equality is identity."""

    tonality: np.ndarray
    snr: np.ndarray
    nb: np.ndarray
    pre_echo_limit: np.ndarray
    thr: np.ndarray


def _require(values: object, name: str, dtype: type, non_negative: bool) -> np.ndarray:
    if not isinstance(values, np.ndarray) or isinstance(values, np.ma.MaskedArray):
        raise ValueError(f"{name} must be a plain NumPy array")
    # Read a subclass (np.matrix, overridden indexing or ufuncs) as its plain ndarray data, so validation and the
    # computation below see the same values with ndarray semantics; np.asarray makes a view, never a copy.
    values = np.asarray(values)
    if values.dtype != dtype:
        raise ValueError(f"{name} must have dtype {np.dtype(dtype)}, got {values.dtype}")
    if values.ndim != 2 or values.shape[1] != PARTITIONS:
        raise ValueError(f"{name} must have shape (n, {PARTITIONS}), got {values.shape}")
    for start in range(0, values.shape[0], _CHUNK_CALLS):  # chunked, so validation temporaries stay bounded
        block = values[start : start + _CHUNK_CALLS]
        if not np.isfinite(block).all():
            raise ValueError(f"{name} must be finite")
        if non_negative and (block < 0).any():
            raise ValueError(f"{name} must be non-negative")
    return values


def _maximum(x, y):
    return np.where(x > y, x, y)  # l3psy.c: #define maximum(x,y) ( (x>y) ? x : y )


def _minimum(x, y):
    return np.where(x < y, x, y)  # l3psy.c: #define minimum(x,y) ( (x<y) ? x : y )


def dist10_partition_thresholds(ecb: np.ndarray, log_ratio: np.ndarray) -> PartitionThresholds:
    """Partition thresholds of one clip from ``ecb`` (float32) and ``log_ratio`` (float64), each ``(calls, 63)`` in
    call order (for example ``SpreadTonalityInput.ecb`` and ``.log_ratio``).

    The pre-echo history starts at zero for this clip and is carried through all calls; separate invocations share no
    state. Raises ``ValueError`` for non-ndarray or masked inputs, other dtypes or shapes, mismatched call counts,
    non-finite values or a negative ``ecb``. ``nb <= ecb`` (``norm <= 0.951``, ``SNR >= 0`` dB), so no value can
    overflow; underflow in the float32 ``nb`` store is the reference's rounding and is not an error under any NumPy
    error policy. Inputs are never modified.
    """
    e = _require(ecb, "ecb", np.float32, non_negative=True)
    c = _require(log_ratio, "log_ratio", np.float64, non_negative=False)
    if e.shape[0] != c.shape[0]:
        raise ValueError(f"ecb and log_ratio must have the same number of calls, got {e.shape[0]} and {c.shape[0]}")
    calls = e.shape[0]
    shape = (calls, PARTITIONS)
    tonality = np.zeros(shape, dtype=np.float64)
    snr = np.zeros(shape, dtype=np.float64)
    nb = np.zeros(shape, dtype=np.float32)
    limit = np.zeros(shape, dtype=np.float64)
    thr = np.zeros(shape, dtype=np.float64)
    history = np.zeros((2, PARTITIONS), dtype=np.float64)  # nb of the second previous and the previous call
    for start in range(0, calls, _CHUNK_CALLS):
        rows = slice(start, min(start + _CHUNK_CALLS, calls))
        with np.errstate(under="ignore"):  # the float32 store of a tiny nb rounds as the reference does
            raw = TONALITY_OFFSET + TONALITY_SLOPE * c[rows]
            tbb = _minimum(1.0, _maximum(0.0, raw))
            tonal = TONE_MASKING_NOISE_DB * tbb + NOISE_MASKING_TONE_DB * (1.0 - tbb)
            snr_rows = _maximum(_MINVAL, tonal)
            nb_rows = ((e[rows].astype(np.float64) * _NORM) * np.exp(-snr_rows * LN_TO_LOG10)).astype(np.float32)
        extended = np.concatenate([history, nb_rows.astype(np.float64)])  # rows: k-2, k-1, k, ...
        nb_1, nb_2 = extended[1:-1], extended[:-2]
        limit_rows = _minimum(
            nb_rows.astype(np.float64), _minimum(PRE_ECHO_PREVIOUS * nb_1, PRE_ECHO_SECOND_PREVIOUS * nb_2)
        )
        tonality[rows], snr[rows], nb[rows], limit[rows] = tbb, snr_rows, nb_rows, limit_rows
        thr[rows] = _maximum(_QTHR, limit_rows)
        history = extended[-2:].copy()
    for array in (tonality, snr, nb, limit, thr):
        array.flags.writeable = False
    return PartitionThresholds(tonality=tonality, snr=snr, nb=nb, pre_echo_limit=limit, thr=thr)
