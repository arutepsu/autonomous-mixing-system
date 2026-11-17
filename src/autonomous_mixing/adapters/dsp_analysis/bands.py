"""Long scale-factor-band mapping of the dist10 front end (front-end contract §3.6; Task 3N).

Profile v1 (44.1 kHz). Following ISO dist10 ``L3psycho_anal`` (l3psy.c, original ll. 652-660), per call and band
sb = 0..20, in double::

    v_band[sb] = w1[sb] v[bu[sb]] + w2[sb] v[bo[sb]]          # the two weighted endpoints first
    v_band[sb] += v[b]   for b = bu[sb] + 1 .. bo[sb] - 1     # then the interior partitions, ascending

applied to the partition energy ``eb`` (giving ``en``) and to the final partition threshold ``thr`` after the
pre-echo limit and the quiet floor (giving ``thm``). The 21 bands cover partitions 0..61 and share their endpoints
(``bo[sb] = bu[sb + 1]``); partition 62 is never read. The summation order is fixed by the reference: it is
observable in the capture (an interior-first order changes ``en`` on all 92 Probe B calls).

Tables: ``bu``, ``bo``, ``w1`` and ``w2`` of the pinned ISO dist10 ``tables/psy_data`` (SHA-256
``9094946e8cc36a30d956571ab3731e57af6e0ce7127a0d33edf11d91a3ef0c48``), block ``freq=44100.0 sb=20``, rows sb=0..20,
read by ``L3para_read`` with ``fscanf("sb=%d cbw=%d bu=%d bo=%d w1=%lf w2=%lf")`` (ll. 938-968) and embedded here as
the values written there. ``bu`` and ``bo`` are used as recorded: band 0 has ``bo = 4`` although ``bu + cbw = 3``
(the source checks ``bo = bu + cbw`` only from sb 1 on), so partition 3 is interior to band 0 and partition 4 its
upper endpoint.

Every call is mapped in order, including start-up calls and calls on which the encoder takes the short-block path
(the fixed long-band mapping of contract §2.2 option (b)); frame eligibility and exact silence are the caller's
concern. Against Probe B with Probe H's captured ``eb``/``thr`` the mapping is exact on all 92 calls.
"""

from dataclasses import dataclass

import numpy as np

PARTITIONS = 63
BANDS = 21
# tables/psy_data, "freq=44100.0 sb=20", sb=0..20: fields bu, bo, w1 and w2
DIST10_LONG_BU_44100 = (0, 4, 7, 11, 14, 17, 18, 21, 24, 27, 30, 33, 36, 40, 43, 46, 49, 52, 55, 57, 59)
DIST10_LONG_BO_44100 = (4, 7, 11, 14, 17, 18, 21, 24, 27, 30, 33, 36, 40, 43, 46, 49, 52, 55, 57, 59, 61)
DIST10_LONG_W1_44100 = (
    1.000, 0.944, 0.389, 0.833, 0.278, 0.861, 0.083, 0.417, 0.750, 0.194, 0.426, 0.463, 0.180, 0.900, 0.532, 0.376,
    0.550, 0.448, 0.597, 0.357, 0.278,
)  # fmt: skip
DIST10_LONG_W2_44100 = (
    0.056, 0.611, 0.167, 0.722, 0.139, 0.917, 0.583, 0.250, 0.805, 0.574, 0.537, 0.819, 0.100, 0.468, 0.623, 0.450,
    0.552, 0.403, 0.643, 0.722, 0.960,
)  # fmt: skip
_CHUNK_CALLS = 1024


@dataclass(frozen=True, eq=False)
class LongBandValues:
    """Per-call long scale-factor-band values (adapter-local, not a core record): ``en`` (band energy, from ``eb``)
    and ``thm`` (band threshold, from ``thr``), each float64 ``(calls, 21)``, freshly allocated and read-only.
    Equality is identity."""

    en: np.ndarray
    thm: np.ndarray


def _require(values: object, name: str) -> np.ndarray:
    if not isinstance(values, np.ndarray) or isinstance(values, np.ma.MaskedArray):
        raise ValueError(f"{name} must be a plain NumPy array")
    values = np.asarray(values)  # a subclass (np.matrix, overridden indexing) is read as its plain data; no copy
    if values.dtype != np.float64:
        raise ValueError(f"{name} must have dtype float64, got {values.dtype}")
    if values.ndim != 2 or values.shape[1] != PARTITIONS:
        raise ValueError(f"{name} must have shape (n, {PARTITIONS}), got {values.shape}")
    for start in range(0, values.shape[0], _CHUNK_CALLS):  # chunked, so validation temporaries stay bounded
        block = values[start : start + _CHUNK_CALLS]
        if not np.isfinite(block).all():
            raise ValueError(f"{name} must be finite")
        if (block < 0).any():
            raise ValueError(f"{name} must be non-negative")
    return values


def _map_rows(rows: np.ndarray, out: np.ndarray) -> None:
    for sb in range(BANDS):
        bu, bo = DIST10_LONG_BU_44100[sb], DIST10_LONG_BO_44100[sb]
        total = DIST10_LONG_W1_44100[sb] * rows[:, bu] + DIST10_LONG_W2_44100[sb] * rows[:, bo]
        for b in range(bu + 1, bo):  # ascending interior, after the endpoints
            total = total + rows[:, b]
        out[:, sb] = total


def dist10_long_bands(eb: np.ndarray, thr: np.ndarray) -> LongBandValues:
    """Map partition energies ``eb`` and final partition thresholds ``thr`` (float64, each ``(calls, 63)``, aligned
    call by call) to the 21 long scale-factor bands.

    Raises ``ValueError`` for non-ndarray or masked inputs, other dtypes or shapes, mismatched call counts, non-finite
    or negative values, and a mapped sum beyond the float64 range (naming the quantity, first call and band) whatever
    the caller's NumPy error policy. Underflow (a weighted endpoint rounding to a subnormal or to zero) is ordinary
    double rounding and is never an error. Other ndarray subclasses are read as plain arrays. Inputs are never
    modified.
    """
    e = _require(eb, "eb")
    t = _require(thr, "thr")
    if e.shape[0] != t.shape[0]:
        raise ValueError(f"eb and thr must have the same number of calls, got {e.shape[0]} and {t.shape[0]}")
    calls = e.shape[0]
    en = np.zeros((calls, BANDS), dtype=np.float64)
    thm = np.zeros((calls, BANDS), dtype=np.float64)
    for start in range(0, calls, _CHUNK_CALLS):
        rows = slice(start, min(start + _CHUNK_CALLS, calls))
        for label, values, out in (("en", e, en), ("thm", t, thm)):
            # The caller's NumPy policy must not change the result. Underflow (a weighted endpoint rounding to a
            # subnormal or to zero) is ordinary IEEE double rounding, as in the reference; overflow can only give an
            # infinite sum from finite non-negative inputs, and that is rejected below.
            with np.errstate(over="ignore", under="ignore"):
                _map_rows(values[rows], out[rows])
            overflowed = np.isinf(out[rows])
            if overflowed.any():
                row, band = np.argwhere(overflowed)[0]
                raise ValueError(f"{label} exceeds the float64 range at call {start + int(row)}, band {int(band)}")
    for array in (en, thm):
        array.flags.writeable = False
    return LongBandValues(en=en, thm=thm)
