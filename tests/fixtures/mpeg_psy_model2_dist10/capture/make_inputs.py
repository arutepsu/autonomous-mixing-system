"""Deterministic int16 test signals for the dist10 Model 2 fixture (unit-test signals, not musical material).

Tone samples are round(amplitude * sin(2*pi*f*n/fs)) for n >= onset (0 before), computed in float64 with
Python's round-half-to-even; impulse signals are one sample of the given amplitude at the onset index, and
amplitude 0 gives exact digital silence. All are stored as mono int16 little-endian ``<name>.raw`` files at
44100 Hz, whole granules of 576 samples unless ``truncate_to`` cuts the clip to that many samples (final-frame
case: the clip then ends partway through a granule). The written
``inputs.json`` records each formula and the SHA-256 of the raw bytes, so the exact samples can be regenerated.

Usage: python make_inputs.py <output directory>
"""

import hashlib
import json
import math
import struct
import sys

FS = 44100
GRANULE = 576

SIGNALS = {
    # level / normalization case: loud pair and the same pair 40 dB lower (amplitude / 100, then rounded)
    "masker_1k_a8192": dict(freq_hz=1000.0, amplitude=8192.0, onset=0, granules=12),
    "target_2k_a1024": dict(freq_hz=2000.0, amplitude=1024.0, onset=0, granules=12),
    "masker_1k_a81.92": dict(freq_hz=1000.0, amplitude=81.92, onset=0, granules=12),
    "target_2k_a10.24": dict(freq_hz=2000.0, amplitude=10.24, onset=0, granules=12),
    # frame-alignment case: silence, then a 1 kHz burst from sample 2000 (= 3 * 576 + 272, not on a granule edge)
    "burst_1k_a8192_onset2000": dict(freq_hz=1000.0, amplitude=8192.0, onset=2000, granules=8),
    # sample-exact window edge: call k's long FFT covers stream samples [576k - 768, 576k + 256); for k = 3 the
    # last included sample is 1983. A single impulse at 1983 must reach call 3, one at 1984 must not.
    "impulse_32767_at1983": dict(freq_hz=None, amplitude=32767.0, onset=1983, granules=6),
    "impulse_32767_at1984": dict(freq_hz=None, amplitude=32767.0, onset=1984, granules=6),
    # silent-masker control: exact digital silence (all samples 0), same length as the level-case pair
    "silence_12granules": dict(freq_hz=None, amplitude=0.0, onset=0, granules=12),
    # final-frame case: the 2 kHz target cut to 6536 = 11 * 576 + 200 samples. Call 10's window ends at 6016
    # (inside the clip), call 11's at 6592 (past the end), so call 11 sees 56 padding zeros.
    "target_2k_a1024_trunc6536": dict(freq_hz=2000.0, amplitude=1024.0, onset=0, granules=12, truncate_to=6536),
}


def samples(freq_hz, amplitude, onset, granules, truncate_to=None):
    out = []
    for n in range(granules * GRANULE if truncate_to is None else truncate_to):
        if freq_hz is None:  # single impulse
            out.append(int(amplitude) if n == onset else 0)
            continue
        value = 0 if n < onset else round(amplitude * math.sin(2.0 * math.pi * freq_hz * n / FS))
        assert -32768 <= value <= 32767
        out.append(int(value))
    return out


def main(out_dir):
    manifest = {}
    for name, spec in SIGNALS.items():
        data = samples(**spec)
        raw = struct.pack(f"<{len(data)}h", *data)
        with open(f"{out_dir}/{name}.raw", "wb") as handle:
            handle.write(raw)
        manifest[name] = dict(spec, sample_rate_hz=FS, frames=len(data), sha256_int16le=hashlib.sha256(raw).hexdigest())
    with open(f"{out_dir}/inputs.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
