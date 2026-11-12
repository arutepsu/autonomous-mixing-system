"""Long-window spectral kernel of the dist10 front end (front-end contract revision 3, §3.1; Task 3C).

Input: the int16 long-block windows of ``framing.frame_long_blocks`` (shape ``(calls, 1024)``), every call
including the start-up calls, in call order. Per call, following ISO dist10 ``L3psycho_anal`` and ``fft``:

1. Window: ``w[i] = 0.5 (1 - cos(2 PI (i - 0.5) / 1024))`` with dist10's ``PI = 3.14159265358979``, computed in
   float64 and stored as float32 (it equals the captured coefficients exactly).
2. Windowed samples: float32 product of the window and the int16 samples (exact against the capture).
3. Unnormalised float64 forward FFT ``X[k] = sum_n x[n] exp(-2 pi i k n / 1024)`` of the float32 windowed samples
   (a real-input transform; only natural-order bins 0-512 are formed, the bins the encoder reads).
4. Pre-floor energy ``|X[k]|^2`` formed in float64 and rounded to float32 before the floor predicate.
5. Floor, bins 0-511 only (dist10's even pre-reorder positions): where the float32 energy widened to float64 is
   ``<= 0.0005`` it is replaced by float32(0.0005). Nyquist (bin 512) is never floored.
6. Phase ``angle(X[k])`` for bins 0-5 only (the bins the long-FFT predictor uses), stored as float32, and set to
   zero on floored bins (dist10 sets ``phi = 0`` there).

Numerical deviation from the reference (project choice in the task): dist10 runs the FFT in float32 storage with
double twiddles; this kernel uses a float64 FFT. Against the Probe C capture the pre-floor energy agrees within
1e-6 of each call's peak bin energy, which the fixture tests check. A bin whose energy lies within float32
rounding of 0.0005 could be floored differently from dist10; the tests report any such bin.

No short-window FFT, tonality, spreading, threshold, WAV conversion or diagnosis here.
"""

from dataclasses import dataclass

import numpy as np

LONG_FFT_SIZE = 1024
CONSUMED_BINS = 513  # HBLKSIZE: natural bins 0..512 (DC .. Nyquist) are read by L3psycho_anal
FLOOR_BINS = 512  # bins 0..511 can be floored; Nyquist (512) never is
PHASE_BINS = 6  # bins 0..5 feed the long-FFT unpredictability predictor
ENERGY_FLOOR = 0.0005  # subs.c: if (energy[i] <= 0.0005) {phi[i] = 0; energy[i] = 0.0005;}
DIST10_PI = 3.14159265358979  # common.h l. 195
_CHUNK_CALLS = 256  # bounds the float64/complex temporaries to a fixed number of calls

_FLOOR32 = np.float32(ENERGY_FLOOR)


@dataclass(frozen=True, eq=False)
class LongSpectrum:
    """Per-call long-window spectrum (adapter-local, not a core record).

    ``energy_prefloor`` and ``energy`` are float32 shaped ``(calls, 513)`` (natural bins 0-512); ``floored`` is bool
    with the same shape (always false at bin 512); ``phase`` is float32 shaped ``(calls, 6)`` in radians, zero on
    floored bins. Row ``k`` belongs to the k-th input window. All arrays are freshly allocated and read-only.
    Equality is identity (arrays have no single truth value).
    """

    energy_prefloor: np.ndarray
    energy: np.ndarray
    floored: np.ndarray
    phase: np.ndarray


def _read_only(array: np.ndarray) -> np.ndarray:
    array.flags.writeable = False
    return array


def _compute_window() -> np.ndarray:
    i = np.arange(LONG_FFT_SIZE, dtype=np.float64)
    return _read_only((0.5 * (1.0 - np.cos(2.0 * DIST10_PI * (i - 0.5) / LONG_FFT_SIZE))).astype(np.float32))


_WINDOW = _compute_window()


def dist10_long_window() -> np.ndarray:
    """The dist10 long Hann window as float32, shape ``(1024,)``.

    Each call returns a fresh read-only copy: a caller that re-enables writing on it cannot change the private
    window the kernel uses.
    """
    return _read_only(_WINDOW.copy())


def _require_windows(windows: object) -> np.ndarray:
    if not isinstance(windows, np.ndarray):
        raise ValueError(f"windows must be a NumPy array, got {type(windows).__name__}")
    if isinstance(windows, np.ma.MaskedArray):
        raise ValueError("windows must not be a masked array")
    windows = np.asarray(windows)  # plain view of any other subclass; copies nothing
    if windows.dtype.kind != "i" or windows.dtype.itemsize != 2:
        raise ValueError(f"windows must have dtype int16, got {windows.dtype}")
    if windows.ndim != 2 or windows.shape[1] != LONG_FFT_SIZE:
        raise ValueError(f"windows must have shape (calls, {LONG_FFT_SIZE}), got {windows.shape}")
    return windows


def _windowed(values: np.ndarray) -> np.ndarray:
    return _WINDOW * values.astype(np.float32)  # int16 -> float32 is exact; one float32 rounding per product


def apply_long_window(windows: np.ndarray) -> np.ndarray:
    """Float32 windowed samples ``window * samples`` for int16 windows of shape ``(calls, 1024)`` (new array).

    This is the same computation ``long_window_spectrum`` feeds to its FFT.
    """
    return _windowed(_require_windows(windows))


def _require_energy(energy_prefloor: object) -> np.ndarray:
    if not isinstance(energy_prefloor, np.ndarray) or isinstance(energy_prefloor, np.ma.MaskedArray):
        raise ValueError("energy_prefloor must be a plain NumPy array")
    values = np.asarray(energy_prefloor)
    if values.dtype != np.float32:
        raise ValueError(f"energy_prefloor must be float32 (rounded before the floor test), got {values.dtype}")
    if values.ndim != 2 or values.shape[1] != CONSUMED_BINS:
        raise ValueError(f"energy_prefloor must have shape (calls, {CONSUMED_BINS}), got {values.shape}")
    return values


def apply_long_energy_floor(energy_prefloor: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Apply dist10's floor to float32 pre-floor energies of shape ``(calls, 513)``.

    Returns ``(energy, floored)``: new float32 energies with float32(0.0005) on floored bins, and the bool mask.
    The predicate widens each float32 energy to float64 and tests ``<= 0.0005``; only bins 0-511 are eligible.
    No float32 value equals 0.0005 (float32(0.0005) = 5.000000237e-4 lies above it), so ``<=`` and ``<`` agree
    on every float32 input; ``<=`` is kept to mirror the source.
    """
    values = _require_energy(energy_prefloor)
    energy = np.empty(values.shape, dtype=np.float32)
    floored = np.empty(values.shape, dtype=np.bool_)
    for start in range(0, values.shape[0], _CHUNK_CALLS):
        block = slice(start, start + _CHUNK_CALLS)
        _floor_block(values[block], energy[block], floored[block])
    return energy, floored


def _floor_block(prefloor: np.ndarray, energy_out: np.ndarray, floored_out: np.ndarray) -> None:
    """Floor one block of calls into preallocated outputs; temporaries are bounded by the block size."""
    np.less_equal(prefloor.astype(np.float64), ENERGY_FLOOR, out=floored_out)
    floored_out[:, FLOOR_BINS:] = False
    energy_out[...] = prefloor
    energy_out[floored_out] = _FLOOR32


def long_window_spectrum(windows: np.ndarray) -> LongSpectrum:
    """Long-window energies and retained phases for int16 windows of shape ``(calls, 1024)``, in call order.

    Raises ``ValueError`` for a non-ndarray or masked input, a dtype other than int16 (either byte order), or a
    shape other than ``(calls, 1024)``. The input is never modified; zero calls give empty results.
    """
    values = _require_windows(windows)
    calls = values.shape[0]
    prefloor = np.empty((calls, CONSUMED_BINS), dtype=np.float32)
    energy = np.empty((calls, CONSUMED_BINS), dtype=np.float32)
    floored = np.empty((calls, CONSUMED_BINS), dtype=np.bool_)
    phase = np.empty((calls, PHASE_BINS), dtype=np.float32)
    for start in range(0, calls, _CHUNK_CALLS):
        block = slice(start, min(start + _CHUNK_CALLS, calls))
        windowed = _windowed(values[block])
        spectrum = np.fft.rfft(windowed.astype(np.float64), n=LONG_FFT_SIZE, axis=1)  # bins 0..512, no scaling
        prefloor[block] = (spectrum.real**2 + spectrum.imag**2).astype(np.float32)
        _floor_block(prefloor[block], energy[block], floored[block])
        block_phase = np.angle(spectrum[:, :PHASE_BINS]).astype(np.float32)
        block_phase[floored[block, :PHASE_BINS]] = 0.0
        phase[block] = block_phase
    return LongSpectrum(
        energy_prefloor=_read_only(prefloor),
        energy=_read_only(energy),
        floored=_read_only(floored),
        phase=_read_only(phase),
    )
