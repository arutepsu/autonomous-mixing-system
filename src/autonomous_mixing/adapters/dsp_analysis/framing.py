"""Sample framing of the dist10 long-block front end (front-end contract revision 2, §2; Task 3A).

Profile v1: 44,100 Hz, mono, one-dimensional int16 samples (the reference's ``short`` values, no rescaling).
ISO dist10 ``L3psycho_anal`` is called once per 576-sample granule; zero-based call ``k`` analyses the stream
window ``[576k - 768, 576k + 256)`` (l3psy.c ll. 466-512; the short FFTs read inside the same window). A clip
of ``N`` samples gets ``ceil(N / 576)`` calls. Positions before sample 0 and after sample ``N - 1`` (the final
partial granule) read as zero, as the zero-initialised save buffer and the encoder's final-frame fill do.

Every call is kept, including the start-up calls 0 and 1, because the later threshold stage needs their history.
A call is *eligible* for measurement only when ``k >= 2`` and ``576k + 256 <= N``: its window lies inside the
clip and the zero-initialised start-up state is past. ``nonzero_samples`` counts the nonzero int16 values in
each window exactly, so silence is decided from the samples rather than from FFT energy (which has a floor).

This module only frames samples: no FFT, threshold, WAV conversion or diagnosis.
"""

from dataclasses import dataclass
from numbers import Integral

import numpy as np

PROFILE_SAMPLE_RATE_HZ = 44100
GRANULE_SAMPLES = 576
WINDOW_SAMPLES = 1024
LOOKBACK_SAMPLES = 768  # window start relative to the call's granule start (sync_flush in l3psy.c)
FIRST_ELIGIBLE_CALL = 2  # calls 0 and 1 use the zero-initialised pre-echo history


@dataclass(frozen=True, eq=False)
class LongBlockFrames:
    """Per-call framing of one clip (adapter-local, not a core record).

    ``call``, ``window_start``, ``window_end`` and ``nonzero_samples`` are int64 and ``eligible`` is bool, each
    shaped ``(calls,)``; ``windows`` is int16 shaped ``(calls, 1024)`` with zero fill outside the clip. Bounds
    are stream sample positions, end-exclusive; ``sample_count`` is the original ``N``. All arrays are freshly
    allocated and read-only. Equality is identity (arrays have no single truth value).
    """

    sample_count: int
    call: np.ndarray
    window_start: np.ndarray
    window_end: np.ndarray
    eligible: np.ndarray
    nonzero_samples: np.ndarray
    windows: np.ndarray


def _require_samples(samples: object) -> np.ndarray:
    if not isinstance(samples, np.ndarray):
        raise ValueError(f"samples must be a NumPy array, got {type(samples).__name__}")
    if isinstance(samples, np.ma.MaskedArray):
        raise ValueError("samples must not be a masked array")
    samples = np.asarray(samples)  # plain view of any other subclass; copies nothing
    if samples.dtype.kind != "i" or samples.dtype.itemsize != 2:
        raise ValueError(f"samples must have dtype int16, got {samples.dtype}")
    if samples.ndim != 1:
        raise ValueError(f"samples must be one-dimensional (mono), got shape {samples.shape}")
    return samples


def _require_profile_rate(sample_rate_hz: object) -> None:
    if isinstance(sample_rate_hz, bool) or not isinstance(sample_rate_hz, Integral):
        raise ValueError(f"sample_rate_hz must be an integer, got {type(sample_rate_hz).__name__}")
    if sample_rate_hz != PROFILE_SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be {PROFILE_SAMPLE_RATE_HZ} (profile v1), got {sample_rate_hz}")


def _read_only(array: np.ndarray) -> np.ndarray:
    array.flags.writeable = False
    return array


def frame_long_blocks(samples: np.ndarray, *, sample_rate_hz: int) -> LongBlockFrames:
    """Frame a mono int16 clip into the dist10 long-block call windows of profile v1.

    Raises ``ValueError`` for a non-ndarray or masked input, a dtype other than int16 (either byte order is
    accepted and the windows are native int16), a shape other than one-dimensional, or a sample rate other than
    44,100 Hz. The input is never modified; an empty clip yields zero calls.
    """
    values = _require_samples(samples)
    _require_profile_rate(sample_rate_hz)
    n = int(values.size)
    calls = -(-n // GRANULE_SAMPLES)
    stream = np.zeros(LOOKBACK_SAMPLES + calls * GRANULE_SAMPLES, dtype=np.int16)
    stream[LOOKBACK_SAMPLES : LOOKBACK_SAMPLES + n] = values  # converts byte order; source untouched
    call = np.arange(calls, dtype=np.int64)
    # Window k starts at stream position 576k - 768, i.e. at offset 576k of the zero-led buffer; the last window
    # ends at 576(calls - 1) + 1024 <= 768 + 576 calls, so it never leaves the buffer. The strided view allocates
    # nothing (no per-sample index array); only the copied int16 windows are new memory.
    if calls:
        view = np.lib.stride_tricks.sliding_window_view(stream, WINDOW_SAMPLES)[::GRANULE_SAMPLES]
        windows = view[:calls].copy()
    else:  # the 768-sample buffer of an empty clip is shorter than one window
        windows = np.zeros((0, WINDOW_SAMPLES), dtype=np.int16)
    window_start = call * GRANULE_SAMPLES - LOOKBACK_SAMPLES
    window_end = window_start + WINDOW_SAMPLES
    eligible = (call >= FIRST_ELIGIBLE_CALL) & (window_end <= n)
    # Row by row: a whole-array count would build a temporary the size of all windows.
    nonzero = np.fromiter((np.count_nonzero(row) for row in windows), dtype=np.int64, count=calls)
    return LongBlockFrames(
        sample_count=n,
        call=_read_only(call),
        window_start=_read_only(window_start),
        window_end=_read_only(window_end),
        eligible=_read_only(eligible),
        nonzero_samples=_read_only(nonzero),
        windows=_read_only(windows),
    )
