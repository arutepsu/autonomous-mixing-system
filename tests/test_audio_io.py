import numpy as np
import pytest
import soundfile as sf

from autonomous_mixing.adapters.dsp_analysis.io import AudioStem, load_stem, validate_pair


def test_mismatched_sample_rates_rejected():
    a = AudioStem("a.wav", np.zeros((5000, 1)), 44100)
    b = AudioStem("b.wav", np.zeros((5000, 1)), 48000)
    with pytest.raises(ValueError, match="sample rate"):
        validate_pair(a, b)


def test_mismatched_lengths_rejected():
    a = AudioStem("a.wav", np.zeros((5000, 1)), 44100)
    b = AudioStem("b.wav", np.zeros((5100, 1)), 44100)
    with pytest.raises(ValueError, match="identical frame counts"):
        validate_pair(a, b)


def _write(path, data, sr=44100):
    sf.write(path, data, sr)
    return path


def test_load_mono_and_stereo(tmp_path):
    mono = load_stem(_write(tmp_path / "mono.wav", np.zeros(4096)))
    stereo = load_stem(_write(tmp_path / "stereo.wav", np.zeros((4096, 2))))
    assert mono.samples.shape == (4096, 1)
    assert stereo.samples.shape == (4096, 2)
    assert mono.sample_rate == 44100


def test_non_wav_extension_rejected(tmp_path):
    with pytest.raises(ValueError, match="Only WAV"):
        load_stem(tmp_path / "kick.flac")


def test_missing_file_rejected(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        load_stem(tmp_path / "missing.wav")


def test_corrupt_file_rejected(tmp_path):
    path = tmp_path / "corrupt.wav"
    path.write_bytes(b"RIFFgarbage")
    with pytest.raises(ValueError, match="Cannot read WAV"):
        load_stem(path)


def test_non_wav_container_with_wav_extension_rejected(tmp_path):
    path = tmp_path / "disguised.wav"
    sf.write(path, np.zeros(4096), 44100, format="FLAC")
    with pytest.raises(ValueError, match="Not a WAV container"):
        load_stem(path)


def test_too_short_stem_rejected(tmp_path):
    with pytest.raises(ValueError, match="too short"):
        load_stem(_write(tmp_path / "short.wav", np.zeros(100)))


def test_multichannel_stem_rejected(tmp_path):
    with pytest.raises(ValueError, match="mono/stereo"):
        load_stem(_write(tmp_path / "quad.wav", np.zeros((4096, 4))))


def test_non_finite_samples_rejected(tmp_path):
    data = np.zeros(4096, dtype=np.float32)
    data[10] = np.nan
    path = tmp_path / "nan.wav"
    sf.write(path, data, 44100, subtype="FLOAT")
    with pytest.raises(ValueError, match="Non-finite"):
        load_stem(path)
