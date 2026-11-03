import numpy as np
from scipy.signal import stft


def mono(samples: np.ndarray) -> np.ndarray:
    # First-release analysis fold-down; phase-cancellation caveat documented.
    return samples.mean(axis=1)


def rms_dbfs(samples: np.ndarray) -> float | None:
    value = float(np.sqrt(np.mean(np.square(samples))))
    return float(20 * np.log10(value)) if value > 0 else None


def peak_dbfs(samples: np.ndarray) -> float | None:
    value = float(np.max(np.abs(samples)))
    return float(20 * np.log10(value)) if value > 0 else None


def spectral_features(samples: np.ndarray, sr: int, frame_length: int = 2048, hop_length: int = 512):
    x = mono(samples)
    frequencies, times, transform = stft(
        x,
        fs=sr,
        nperseg=frame_length,
        noverlap=frame_length - hop_length,
        boundary=None,
        padded=False,
    )
    return frequencies, times, np.abs(transform)


def coactivity_proxy(kick_mag: np.ndarray, bass_mag: np.ndarray) -> float:
    # Relative threshold per stem: frames >10% of that stem's max spectral activity.
    # Non-validated proxy, NOT a masking score.
    k = np.square(kick_mag).sum(axis=0)
    b = np.square(bass_mag).sum(axis=0)
    if not k.any() or not b.any():
        return 0.0
    return float(np.mean((k > k.max() * 0.1) & (b > b.max() * 0.1)))


def band_power(mag: np.ndarray, frequencies: np.ndarray, lo: float, hi: float) -> float:
    mask = (frequencies >= lo) & (frequencies < hi)
    return float(np.square(mag[mask]).sum(axis=0).mean()) if np.any(mask) else 0.0
