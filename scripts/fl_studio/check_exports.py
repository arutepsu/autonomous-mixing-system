"""Check baseline and candidate FL Studio exports of the gain feasibility trial (technical feasibility test).

Usage (repository root, repository environment):

    uv run python scripts/fl_studio/check_exports.py --baseline <dir> --candidate <dir> \
        --role kick=<file.wav> --role bass=<file.wav> --role master=<file.wav> --changed bass [--out <report.json>]

Each role names the same file name in both export directories (FL Studio's split export names stems after their
mixer inserts), or ``role=<baseline.wav>:<candidate.wav>`` when the names differ, as with FL Studio's project-name
prefixes (``trap-baseline_Kick.wav``, ``trap-candidate_Kick.wav``); the directories may then be the same. Files are only read, never modified or converted. The report records, per file: bytes, SHA-256,
container, subtype, sample rate, channels, frames, and whether it meets the dist10 masking profile v1 (WAV, mono,
44,100 Hz, PCM_16; contract §2.3), listing each mismatch. Across files: whether all six share one sample rate and
one frame count. Per role, how the baseline/candidate timeline alignment was established:
  - ``bit_identical``: decoded samples equal exactly (expected for the roles the change does not touch);
  - ``lag_samples``: the lag of the peak of the cross-correlation of the channel means within +/- ``max_lag``
    samples (0 means no offset was found at sample resolution);
  - for the changed role, ``gain_db``: the least-squares gain of candidate over baseline at lag 0
    (20 log10 of sum(b c) / sum(b b)) and ``residual_ratio``, the residual energy relative to the candidate's.
Numbers are reported, not judged: no tolerance is applied. The master is a mixture, so only identity and lag are
reported for it.
"""

import argparse
import hashlib
import json
import math
import pathlib
import sys

import numpy as np
import soundfile as sf

PROFILE = {"container": ("WAV", "WAVEX", "RF64"), "channels": 1, "samplerate": 44100, "subtype": "PCM_16"}
DEFAULT_MAX_LAG = 4410  # search window for the lag report (100 ms at 44.1 kHz); a search range, not a threshold


def describe(path: pathlib.Path) -> dict:
    data = path.read_bytes()
    info = sf.info(str(path))
    mismatches = []
    if info.format not in PROFILE["container"]:
        mismatches.append(f"container {info.format}")
    if info.channels != PROFILE["channels"]:
        mismatches.append(f"{info.channels} channels")
    if info.samplerate != PROFILE["samplerate"]:
        mismatches.append(f"{info.samplerate} Hz")
    if info.subtype != PROFILE["subtype"]:
        mismatches.append(f"subtype {info.subtype}")
    return {
        "file": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "container": info.format,
        "subtype": info.subtype,
        "samplerate": info.samplerate,
        "channels": info.channels,
        "frames": info.frames,
        "meets_profile_v1": not mismatches,
        "profile_mismatches": mismatches,
    }


def _mono(path: pathlib.Path) -> np.ndarray:
    samples, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return samples.mean(axis=1)


def lag_samples(baseline: np.ndarray, candidate: np.ndarray, max_lag: int) -> int | None:
    """Lag (candidate relative to baseline) of the cross-correlation peak within +/- max_lag; None if silent."""
    n = max(baseline.size, candidate.size)
    if n == 0 or not baseline.any() or not candidate.any():
        return None
    size = 1 << (2 * n - 1).bit_length()
    spectrum = np.fft.rfft(candidate, size) * np.conj(np.fft.rfft(baseline, size))
    correlation = np.fft.irfft(spectrum, size)
    lags = np.arange(-max_lag, max_lag + 1)
    values = correlation[lags % size]
    return int(lags[int(np.argmax(values))])


def compare(baseline: pathlib.Path, candidate: pathlib.Path, changed: bool, max_lag: int) -> dict:
    b, c = _mono(baseline), _mono(candidate)
    result = {
        "bit_identical": bool(b.shape == c.shape and np.array_equal(b, c)),
        "lag_samples": lag_samples(b, c, max_lag),
    }
    if changed:
        if b.shape != c.shape:
            result["gain_db"] = None
            result["residual_ratio"] = None
            result["note"] = "frame counts differ; no gain estimate"
        else:
            energy = float(np.dot(b, b))
            ratio = float(np.dot(b, c)) / energy if energy else math.nan
            residual = c - ratio * b
            candidate_energy = float(np.dot(c, c))
            result["gain_db"] = 20.0 * math.log10(ratio) if ratio > 0 else None
            result["residual_ratio"] = (
                float(np.dot(residual, residual)) / candidate_energy if candidate_energy else None
            )
    return result


def check(
    baseline_dir: pathlib.Path, candidate_dir: pathlib.Path, roles: dict[str, str], changed: str, max_lag: int
) -> dict:
    if changed not in roles:
        raise ValueError(f"changed role {changed!r} is not among the roles {sorted(roles)}")
    files = {
        condition: {role: describe(directory / _name(name, side)) for role, name in roles.items()}
        for side, (condition, directory) in enumerate((("baseline", baseline_dir), ("candidate", candidate_dir)))
    }
    every = [entry for condition in files.values() for entry in condition.values()]
    alignment = {
        role: compare(baseline_dir / _name(name, 0), candidate_dir / _name(name, 1), role == changed, max_lag)
        for role, name in roles.items()
    }
    return {
        "changed_role": changed,
        "files": files,
        "common_samplerate": len({e["samplerate"] for e in every}) == 1,
        "common_frames": len({e["frames"] for e in every}) == 1,
        "all_meet_profile_v1": all(e["meets_profile_v1"] for e in every),
        "alignment": alignment,
        "max_lag_searched": max_lag,
    }


def _name(spec: str, side: int) -> str:
    """File name of one condition: ``same.wav`` for both, or ``baseline.wav:candidate.wav``."""
    names = spec.split(":")
    return names[side] if len(names) == 2 else spec


def _roles(values: list[str]) -> dict[str, str]:
    roles = {}
    for value in values:
        role, sep, name = value.partition("=")
        parts = name.split(":")
        if not sep or not role or role in roles or len(parts) > 2 or not all(parts):
            raise SystemExit(
                f"--role must be role=file.wav or role=baseline.wav:candidate.wav, each role once: {value!r}"
            )
        roles[role] = name
    return roles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", type=pathlib.Path, required=True)
    parser.add_argument("--candidate", type=pathlib.Path, required=True)
    parser.add_argument(
        "--role", action="append", required=True, help="role=file.wav, or role=baseline.wav:candidate.wav"
    )
    parser.add_argument("--changed", required=True, help="the role whose insert gain was changed")
    parser.add_argument("--max-lag", type=int, default=DEFAULT_MAX_LAG)
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args(argv)
    report = check(args.baseline, args.candidate, _roles(args.role), args.changed, args.max_lag)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
