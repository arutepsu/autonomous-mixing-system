"""Spectral unpredictability ``cw`` of the dist10 front end (front-end contract §3.2; Task 3F).

Inputs are the post-floor spectra of ``long_spectrum`` and ``short_spectrum`` for one clip, in call order, every
call including the start-up calls. Output: float64 ``cw`` shaped ``(calls, 513)``, following ISO dist10
``L3psycho_anal`` (l3psy.c, original ll. 484-546):

* Lines 0-5, long predictor with history. The reference keeps ``r`` and ``phi_sav`` in two float32 age slots; each
  call flips the ages, reads ``old`` (the previous call's stored values) and ``oldest`` (two calls back, held in the
  slot about to receive the new value) before overwriting that slot with ``r_new = float32(sqrt(energy))`` and
  ``phi_new = phase``. Then, in double: ``r' = 2 r_old - r_oldest``, ``phi' = 2 phi_old - phi_oldest``,
  ``cw = sqrt((r_new cos phi_new - r' cos phi')^2 + (r_new sin phi_new - r' sin phi')^2) / (r_new + |r'|)``, with the
  float32 ``r_new`` read back from its slot. History starts at zero for every clip.
* Lines 6-205, short predictor without history: for j = 6, 10, ..., 202 and short bin ``k = (j + 2) // 4``, block 1
  of the same call is predicted from ``2 r(block 0) - r(block 2)`` and ``2 phi(block 0) - phi(block 2)``, all in
  double from the float32 short spectra; lines j..j+3 share the value.
* Lines 206-512: the constant 0.4.

Zero denominator: like the reference, ``cw = 0`` where ``r_new + |r'| == 0`` (or ``r2 + |r'| == 0``). Spectra from
the floored kernels never reach it: bins 0-5 and short bins 2-51 lie in the floored ranges, so ``r_new`` and ``r2``
are at least ``sqrt(float32(0.0005))`` (about 0.0224). Only unfloored inputs such as zero energies reach it.

No partitioning, spreading, tonality, threshold or diagnosis here.
"""

import numpy as np

LONG_PREDICTOR_BINS = 6
SHORT_LINE_START = 6
CONSTANT_LINE_START = 206
UNPREDICTABILITY_LINES = 513  # HBLKSIZE
CONSTANT_UNPREDICTABILITY = 0.4
LONG_BINS = 513
SHORT_BLOCKS = 3
SHORT_BINS = 129
_GROUP = 4


def short_bin_for_line(line: int) -> int:
    """Short-FFT bin used for unpredictability line ``line`` in 6..205 (dist10: k = (j + 2) / 4 for j = 6, 10, ...)."""
    first = SHORT_LINE_START + ((line - SHORT_LINE_START) // _GROUP) * _GROUP
    return (first + 2) // 4


_SHORT_GROUP_STARTS = np.arange(SHORT_LINE_START, CONSTANT_LINE_START, _GROUP)  # 6, 10, ..., 202
_SHORT_GROUP_BINS = (_SHORT_GROUP_STARTS + 2) // 4  # 2 .. 51


def _require(values: object, name: str, shape_tail: tuple[int, ...], non_negative: bool) -> np.ndarray:
    if not isinstance(values, np.ndarray) or isinstance(values, np.ma.MaskedArray):
        raise ValueError(f"{name} must be a plain NumPy array")
    values = np.asarray(values)
    if values.dtype != np.float32:
        raise ValueError(f"{name} must have dtype float32, got {values.dtype}")
    if values.ndim != 1 + len(shape_tail) or values.shape[1:] != shape_tail:
        raise ValueError(f"{name} must have shape (calls, {', '.join(map(str, shape_tail))}), got {values.shape}")
    if not np.isfinite(values).all():
        raise ValueError(f"{name} must be finite")
    if non_negative and (values < 0).any():
        raise ValueError(f"{name} must be non-negative")
    return values


def _ratio(t1: np.ndarray, t2: np.ndarray, t3: np.ndarray) -> np.ndarray:
    out = np.zeros(t3.shape, dtype=np.float64)  # the reference's zero-denominator value
    nonzero = t3 != 0.0
    out[nonzero] = np.sqrt(t1[nonzero] * t1[nonzero] + t2[nonzero] * t2[nonzero]) / t3[nonzero]
    return out


def long_predictor_cw(r_new, phi_new, r_old, r_oldest, phi_old, phi_oldest) -> np.ndarray:
    """Single-step long predictor: float32 arrays ``(n, 6)`` of the stored slot values -> float64 ``cw`` ``(n, 6)``.

    ``r_new``/``phi_new`` are the values just stored for this call; the other four are the slots read before it.
    """
    values = [
        _require(v, n, (LONG_PREDICTOR_BINS,), n.startswith("r"))
        for v, n in zip(
            (r_new, phi_new, r_old, r_oldest, phi_old, phi_oldest),
            ("r_new", "phi_new", "r_old", "r_oldest", "phi_old", "phi_oldest"),
            strict=True,
        )
    ]
    if len({v.shape[0] for v in values}) != 1:
        raise ValueError("long predictor inputs must have the same number of rows")
    rn, pn, ro, roo, po, poo = (v.astype(np.float64) for v in values)
    r_prime = 2.0 * ro - roo
    phi_prime = 2.0 * po - poo
    t1 = rn * np.cos(pn) - r_prime * np.cos(phi_prime)
    t2 = rn * np.sin(pn) - r_prime * np.sin(phi_prime)
    return _ratio(t1, t2, rn + np.abs(r_prime))


def short_predictor_cw(short_energy, short_phase) -> np.ndarray:
    """Short predictor for lines 6..205: float32 ``(calls, 3, 129)`` energy and phase -> float64 ``(calls, 200)``."""
    energy = _require(short_energy, "short_energy", (SHORT_BLOCKS, SHORT_BINS), True)
    phase = _require(short_phase, "short_phase", (SHORT_BLOCKS, SHORT_BINS), False)
    if energy.shape[0] != phase.shape[0]:
        raise ValueError("short_energy and short_phase must have the same number of calls")
    e = energy[:, :, _SHORT_GROUP_BINS].astype(np.float64)  # (calls, 3, 50)
    p = phase[:, :, _SHORT_GROUP_BINS].astype(np.float64)
    r_prime = 2.0 * np.sqrt(e[:, 0]) - np.sqrt(e[:, 2])
    phi_prime = 2.0 * p[:, 0] - p[:, 2]
    r2, phi2 = np.sqrt(e[:, 1]), p[:, 1]
    t1 = r2 * np.cos(phi2) - r_prime * np.cos(phi_prime)
    t2 = r2 * np.sin(phi2) - r_prime * np.sin(phi_prime)
    return np.repeat(_ratio(t1, t2, r2 + np.abs(r_prime)), _GROUP, axis=1)


_CHUNK_CALLS = 256  # bounds the per-call float32 history copies and float64 temporaries


class _TwoSlotHistory:
    """The reference's two float32 age slots for one clip, advanced chunk by chunk (state carries across chunks)."""

    def __init__(self) -> None:
        self.r_slots = np.zeros((2, LONG_PREDICTOR_BINS), dtype=np.float32)  # r[chn][age][j], zero at clip start
        self.phi_slots = np.zeros((2, LONG_PREDICTOR_BINS), dtype=np.float32)
        self.new, self.old, self.oldest = 0, 1, 0  # static initial ages in l3psy.c

    def advance(self, long_energy: np.ndarray, long_phase: np.ndarray) -> dict[str, np.ndarray]:
        """Process the next calls in order; return their slot values (float32, one row per call)."""
        calls = long_energy.shape[0]
        out = {key: np.empty((calls, LONG_PREDICTOR_BINS), dtype=np.float32) for key in
               ("r_new", "phi_new", "r_old", "r_oldest", "phi_old", "phi_oldest")}  # fmt: skip
        stored_r = np.sqrt(long_energy[:, :LONG_PREDICTOR_BINS].astype(np.float64)).astype(np.float32)
        r_slots, phi_slots = self.r_slots, self.phi_slots
        for call in range(calls):
            self.new, self.old, self.oldest = (1, 0, 1) if self.new == 0 else (0, 1, 0)  # flip once per call
            new, old, oldest = self.new, self.old, self.oldest
            out["r_old"][call], out["r_oldest"][call] = r_slots[old], r_slots[oldest]  # read before overwrite
            out["phi_old"][call], out["phi_oldest"][call] = phi_slots[old], phi_slots[oldest]
            r_slots[new], phi_slots[new] = stored_r[call], long_phase[call]
            out["r_new"][call], out["phi_new"][call] = r_slots[new], phi_slots[new]
        return out


def dist10_unpredictability(long_energy, long_phase, short_energy, short_phase) -> np.ndarray:
    """dist10 ``cw`` for one clip: float64 ``(calls, 513)``, read-only, rows in call order, history from zero.

    ``long_energy`` float32 ``(calls, 513)`` and ``long_phase`` float32 ``(calls, 6)`` are ``LongSpectrum.energy`` and
    ``.phase``; ``short_energy``/``short_phase`` float32 ``(calls, 3, 129)`` are ``ShortSpectrum.energy`` and ``.phase``.
    Raises ``ValueError`` for non-ndarray or masked inputs, other dtypes or shapes, mismatched call counts,
    non-finite values or negative energies. Inputs are never modified.
    """
    l_energy = _require(long_energy, "long_energy", (LONG_BINS,), True)
    l_phase = _require(long_phase, "long_phase", (LONG_PREDICTOR_BINS,), False)
    s_energy = _require(short_energy, "short_energy", (SHORT_BLOCKS, SHORT_BINS), True)
    s_phase = _require(short_phase, "short_phase", (SHORT_BLOCKS, SHORT_BINS), False)
    counts = {a.shape[0] for a in (l_energy, l_phase, s_energy, s_phase)}
    if len(counts) != 1:
        raise ValueError(f"inputs must have the same number of calls, got {sorted(counts)}")
    calls = l_energy.shape[0]
    cw = np.full((calls, UNPREDICTABILITY_LINES), CONSTANT_UNPREDICTABILITY, dtype=np.float64)
    history = _TwoSlotHistory()  # one per clip: zero history at call 0
    for start in range(0, calls, _CHUNK_CALLS):
        block = slice(start, min(start + _CHUNK_CALLS, calls))
        h = history.advance(l_energy[block], l_phase[block])
        cw[block, :LONG_PREDICTOR_BINS] = long_predictor_cw(
            h["r_new"], h["phi_new"], h["r_old"], h["r_oldest"], h["phi_old"], h["phi_oldest"]
        )
        cw[block, SHORT_LINE_START:CONSTANT_LINE_START] = short_predictor_cw(s_energy[block], s_phase[block])
    cw.flags.writeable = False
    return cw
