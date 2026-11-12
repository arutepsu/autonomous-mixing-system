"""Short-window spectral kernel of the dist10 front end (front-end contract §3.2; Task 3E).

Input: the int16 long-block windows of ``framing.frame_long_blocks`` (shape ``(calls, 1024)``), every call
including the start-up calls, in call order. Per call and block, following ISO dist10 ``L3psycho_anal`` and ``fft``:

1. Blocks: three overlapping 256-sample blocks at long-window positions 256, 384 and 512 (``savebuf[128 (2 + s) + j]``),
   i.e. stream samples ``[576k - 512 + 128 s, +256)``; block 1 shares the long window's centre.
2. Window: ``w_s[j] = 0.5 (1 - cos(2 PI (j - 0.5) / 256))`` with dist10's ``PI = 3.14159265358979``, computed in
   float64 and stored as float32 (equal to the captured coefficients); windowed samples are float32 products.
3. Unnormalised float64 forward FFT of each windowed block; natural short bins 0-128 (DC .. Nyquist) are formed.
4. Pre-floor energy ``|X[k]|^2`` formed in float64 and rounded to float32 before the floor predicate.
5. Floor, bins 0-127 only (dist10's even pre-reorder positions of the 256-point FFT): where the float32 energy
   widened to float64 is ``<= 0.0005`` it is replaced by float32(0.0005) and the phase set to zero. Nyquist (128)
   is never floored.
6. Phase ``angle(X[k])`` for bins 0-128, stored as float32.

Same precision strategy as ``long_spectrum`` (float64 FFT instead of dist10's float32 stages); against the Probe D
capture energy agrees within 1e-6 of each block's peak bin energy, which the fixture tests check. Calls are
processed in chunks, so temporaries do not grow with the clip. The module is self-contained (no import of
``long_spectrum``); the floor rule is the same.

No unpredictability (cw) prediction or history, partitioning, tonality, threshold, WAV conversion or diagnosis.
"""

from dataclasses import dataclass

import numpy as np

LONG_WINDOW_SIZE = 1024
SHORT_FFT_SIZE = 256
SHORT_BLOCKS = 3
SHORT_BLOCK_OFFSETS = (256, 384, 512)  # 128 * (2 + s) within the long window (l3psy.c)
SHORT_CONSUMED_BINS = 129  # HBLKSIZE_s: natural short bins 0..128
SHORT_FLOOR_BINS = 128  # bins 0..127 can be floored; Nyquist (128) never is
SHORT_ENERGY_FLOOR = 0.0005  # subs.c: if (energy[i] <= 0.0005) {phi[i] = 0; energy[i] = 0.0005;}
DIST10_PI = 3.14159265358979  # common.h l. 195
_CHUNK_CALLS = 256

_FLOOR32 = np.float32(SHORT_ENERGY_FLOOR)


@dataclass(frozen=True, eq=False)
class ShortSpectrum:
    """Per-call short-window spectra (adapter-local, not a core record).

    ``energy_prefloor``, ``energy`` and ``phase`` are float32 and ``floored`` bool, each shaped ``(calls, 3, 129)``:
    call, short block 0-2 in order, natural short bin 0-128. ``floored`` is always false at bin 128; ``phase`` is
    zero where floored. All arrays are freshly allocated and read-only. Equality is identity.
    """

    energy_prefloor: np.ndarray
    energy: np.ndarray
    floored: np.ndarray
    phase: np.ndarray


def _read_only(array: np.ndarray) -> np.ndarray:
    array.flags.writeable = False
    return array


def _compute_window() -> np.ndarray:
    j = np.arange(SHORT_FFT_SIZE, dtype=np.float64)
    return _read_only((0.5 * (1.0 - np.cos(2.0 * DIST10_PI * (j - 0.5) / SHORT_FFT_SIZE))).astype(np.float32))


_WINDOW = _compute_window()


def dist10_short_window() -> np.ndarray:
    """The dist10 short Hann window as float32, shape ``(256,)``; a fresh read-only copy on every call."""
    return _read_only(_WINDOW.copy())


def _require_windows(windows: object) -> np.ndarray:
    if not isinstance(windows, np.ndarray):
        raise ValueError(f"windows must be a NumPy array, got {type(windows).__name__}")
    if isinstance(windows, np.ma.MaskedArray):
        raise ValueError("windows must not be a masked array")
    windows = np.asarray(windows)
    if windows.dtype.kind != "i" or windows.dtype.itemsize != 2:
        raise ValueError(f"windows must have dtype int16, got {windows.dtype}")
    if windows.ndim != 2 or windows.shape[1] != LONG_WINDOW_SIZE:
        raise ValueError(f"windows must have shape (calls, {LONG_WINDOW_SIZE}), got {windows.shape}")
    return windows


def _windowed_blocks(values: np.ndarray) -> np.ndarray:
    """Float32 windowed blocks, shape ``(calls, 3, 256)``, for a chunk of int16 long windows."""
    blocks = np.stack([values[:, o : o + SHORT_FFT_SIZE] for o in SHORT_BLOCK_OFFSETS], axis=1)
    return _WINDOW * blocks.astype(np.float32)  # int16 -> float32 exact; one float32 rounding per product


def apply_short_window(windows: np.ndarray) -> np.ndarray:
    """Float32 windowed short blocks ``(calls, 3, 256)`` for int16 long windows ``(calls, 1024)`` (new array).

    This is the same computation ``short_window_spectrum`` feeds to its FFT.
    """
    return _windowed_blocks(_require_windows(windows))


def _floor_block(prefloor: np.ndarray, energy_out: np.ndarray, floored_out: np.ndarray) -> None:
    np.less_equal(prefloor.astype(np.float64), SHORT_ENERGY_FLOOR, out=floored_out)
    floored_out[..., SHORT_FLOOR_BINS:] = False
    energy_out[...] = prefloor
    energy_out[floored_out] = _FLOOR32


def short_window_spectrum(windows: np.ndarray) -> ShortSpectrum:
    """Short-window energies and phases for int16 long windows of shape ``(calls, 1024)``, in call and block order.

    Raises ``ValueError`` for a non-ndarray or masked input, a dtype other than int16 (either byte order), or a
    shape other than ``(calls, 1024)``. The input is never modified; zero calls give empty results.
    """
    values = _require_windows(windows)
    calls = values.shape[0]
    shape = (calls, SHORT_BLOCKS, SHORT_CONSUMED_BINS)
    prefloor = np.empty(shape, dtype=np.float32)
    energy = np.empty(shape, dtype=np.float32)
    floored = np.empty(shape, dtype=np.bool_)
    phase = np.empty(shape, dtype=np.float32)
    for start in range(0, calls, _CHUNK_CALLS):
        block = slice(start, min(start + _CHUNK_CALLS, calls))
        spectrum = np.fft.rfft(_windowed_blocks(values[block]).astype(np.float64), n=SHORT_FFT_SIZE, axis=2)
        prefloor[block] = (spectrum.real**2 + spectrum.imag**2).astype(np.float32)
        _floor_block(prefloor[block], energy[block], floored[block])
        chunk_phase = np.angle(spectrum).astype(np.float32)
        chunk_phase[floored[block]] = 0.0
        phase[block] = chunk_phase
    return ShortSpectrum(
        energy_prefloor=_read_only(prefloor),
        energy=_read_only(energy),
        floored=_read_only(floored),
        phase=_read_only(phase),
    )
