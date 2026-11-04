import hashlib
from pathlib import Path

from autonomous_mixing.adapters.dsp_analysis.features import (
    band_power,
    coactivity_proxy,
    peak_dbfs,
    rms_dbfs,
    spectral_features,
)
from autonomous_mixing.adapters.dsp_analysis.io import load_stem, validate_pair


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_pair(kick_path: str | Path, bass_path: str | Path) -> dict:
    kick = load_stem(kick_path)
    bass = load_stem(bass_path)
    validate_pair(kick, bass)
    fk, tk, mk = spectral_features(kick.samples, kick.sample_rate)
    fb, tb, mb = spectral_features(bass.samples, bass.sample_rate)
    return {
        "schema_version": "m0.1",
        "status": "descriptive_analysis_only",
        "note": "Temporal coactivity and band power are exploratory proxies, not validated perceptual masking measures.",
        "sample_rate_hz": kick.sample_rate,
        "duration_seconds": round(len(kick.samples) / kick.sample_rate, 6),
        "configuration": {"stft_frame_length": 2048, "stft_hop_length": 512, "low_band_hz": [30, 200]},
        "kick": {
            "file": str(kick_path),
            "sha256": sha256(kick_path),
            "rms_dbfs": rms_dbfs(kick.samples),
            "peak_dbfs": peak_dbfs(kick.samples),
            "low_band_power_proxy": band_power(mk, fk, 30, 200),
        },
        "bass": {
            "file": str(bass_path),
            "sha256": sha256(bass_path),
            "rms_dbfs": rms_dbfs(bass.samples),
            "peak_dbfs": peak_dbfs(bass.samples),
            "low_band_power_proxy": band_power(mb, fb, 30, 200),
        },
        "temporal_coactivity_fraction": coactivity_proxy(mk, mb),
    }
