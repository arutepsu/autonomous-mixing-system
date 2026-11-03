from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

# libsndfile container names accepted as WAV (RIFF, WAVE_FORMAT_EXTENSIBLE, RF64).
WAV_FORMATS = ("WAV", "WAVEX", "RF64")


@dataclass(frozen=True)
class AudioStem:
    path: str
    samples: np.ndarray  # shape (frames, channels), float64
    sample_rate: int


def load_stem(path: str | Path) -> AudioStem:
    path = Path(path)
    if path.suffix.lower() != ".wav":
        raise ValueError(f"Only WAV input supported: {path}")
    if not path.is_file():
        raise ValueError(f"Stem file not found: {path}")
    try:
        with sf.SoundFile(path) as f:
            if f.format not in WAV_FORMATS:
                raise ValueError(f"Not a WAV container (detected {f.format}): {path}")
            samples = f.read(dtype="float64", always_2d=True)
            sr = f.samplerate
    except sf.LibsndfileError as exc:
        raise ValueError(f"Cannot read WAV file {path}: {exc.error_string}") from exc
    if samples.size == 0 or samples.shape[0] < 2048:
        raise ValueError(f"Stem too short or empty: {path}")
    if samples.shape[1] not in (1, 2):
        raise ValueError(f"Expected mono/stereo stem: {path}")
    if not np.isfinite(samples).all():
        raise ValueError(f"Non-finite samples: {path}")
    return AudioStem(str(path), samples, sr)


def validate_pair(kick: AudioStem, bass: AudioStem) -> None:
    if kick.sample_rate != bass.sample_rate:
        raise ValueError(f"Stems must share a sample rate (kick: {kick.sample_rate} Hz, bass: {bass.sample_rate} Hz)")
    if len(kick.samples) != len(bass.samples):
        raise ValueError(
            "Stems must have identical frame counts and common timeline "
            f"(kick: {len(kick.samples)}, bass: {len(bass.samples)} frames)"
        )
