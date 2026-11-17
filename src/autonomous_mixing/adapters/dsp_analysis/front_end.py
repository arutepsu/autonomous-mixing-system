"""Clip-to-band front end of the dist10 psychoacoustic model (front-end contract §2-§3.6; Task 3O).

Profile v1: one mono int16 clip at 44,100 Hz. The entry point composes the reviewed kernels in the reference's
order, over every call of the clip:

    frame_long_blocks           framing and input validation (contract §2.2)
    long_window_spectrum        long FFT energy and phase (§3.1)
    short_window_spectrum       three short FFTs per call (§3.2)
    dist10_unpredictability     cw from the long and short spectra, with the two-call predictor history (§3.2)
    dist10_partition_sums       eb and cb (§3.3)
    dist10_spread_tonality_input ecb and the tonality input (§3.4)
    dist10_partition_thresholds  thr with the two-call pre-echo history (§3.5)
    dist10_long_bands           en from eb and thm from thr, the fixed long-band mapping (§3.6)

Every call goes through the numerical chain, including the start-up calls 0 and 1, whose history the predictor and
the threshold stage need, and calls on which the encoder would take the short-block path. Ineligible calls and
exactly silent calls (``nonzero_samples == 0``) stay in the result with their metadata; selecting frames, gating
silence and forming masking margins are the caller's concern. Framing windows and intermediate spectra are not kept.

No WAV reading or conversion, no analyzer integration and no interpretation of masking margins here.
"""

from dataclasses import dataclass

import numpy as np

from autonomous_mixing.adapters.dsp_analysis.bands import dist10_long_bands
from autonomous_mixing.adapters.dsp_analysis.framing import frame_long_blocks
from autonomous_mixing.adapters.dsp_analysis.long_spectrum import long_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.partitions import dist10_partition_sums
from autonomous_mixing.adapters.dsp_analysis.short_spectrum import short_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.spreading import dist10_spread_tonality_input
from autonomous_mixing.adapters.dsp_analysis.thresholds import dist10_partition_thresholds
from autonomous_mixing.adapters.dsp_analysis.unpredictability import dist10_unpredictability


@dataclass(frozen=True, eq=False)
class ClipBandAnalysis:
    """Long-band values of one clip, per call in call order (adapter-local, not a core record).

    ``sample_count`` is the clip length N and ``sample_rate_hz`` the profile rate. ``call``, ``window_start``,
    ``window_end`` (stream sample positions, end-exclusive) and ``nonzero_samples`` are int64 and ``eligible`` is
    bool, each ``(calls,)``, as returned by the framer: a call is eligible when ``k >= 2`` and ``576k + 256 <= N``,
    and exactly silent when ``nonzero_samples == 0``. ``en`` (band energy) and ``thm`` (band threshold) are float64
    ``(calls, 21)``. All arrays are read-only. Equality is identity.
    """

    sample_count: int
    sample_rate_hz: int
    call: np.ndarray
    window_start: np.ndarray
    window_end: np.ndarray
    eligible: np.ndarray
    nonzero_samples: np.ndarray
    en: np.ndarray
    thm: np.ndarray


def dist10_clip_to_bands(samples: np.ndarray, *, sample_rate_hz: int) -> ClipBandAnalysis:
    """Long scale-factor-band energies and thresholds of one mono int16 clip (profile v1).

    Inputs are validated by ``frame_long_blocks``: ``ValueError`` for a non-ndarray or masked input, a dtype other
    than int16 (either byte order), a shape other than one-dimensional, or a sample rate other than 44,100 Hz. The
    input is never modified. An empty clip yields zero calls and ``(0, 21)`` band arrays.
    """
    frames = frame_long_blocks(samples, sample_rate_hz=sample_rate_hz)
    long = long_window_spectrum(frames.windows)
    short = short_window_spectrum(frames.windows)
    cw = dist10_unpredictability(long.energy, long.phase, short.energy, short.phase)
    sums = dist10_partition_sums(long.energy, cw)
    spread = dist10_spread_tonality_input(sums.eb, sums.cb)
    thresholds = dist10_partition_thresholds(spread.ecb, spread.log_ratio)
    bands = dist10_long_bands(sums.eb, thresholds.thr)
    return ClipBandAnalysis(
        sample_count=frames.sample_count,
        sample_rate_hz=int(sample_rate_hz),
        call=frames.call,
        window_start=frames.window_start,
        window_end=frames.window_end,
        eligible=frames.eligible,
        nonzero_samples=frames.nonzero_samples,
        en=bands.en,
        thm=bands.thm,
    )
