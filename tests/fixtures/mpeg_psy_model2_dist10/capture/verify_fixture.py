"""Check the dist10 Model 2 fixture without running the reference.

1. Inputs regenerate byte-identically, and every recorded SHA-256 (inputs, the three expected sets, the window
   coefficients, the three probe patches and the probe B and C harness sources) matches the files on disk.
2. Relations between the two captures, all exact (no tolerance):
   - probe B reports the same calls and pe as probe A, and its encoder branch flag equals A's long_path;
   - on every call where the encoder took the long path, probe B's en/thm equal probe A's bit for bit;
   - on every short-path call, probe B has 21 finite positive en/thm values;
   - final-frame case: the truncated clip's calls whose window ends inside the clip equal the full-length
     clip's calls; the call whose window reaches past the end differs;
   - silent-masker control: for digital silence thm == thm_quiet == thm_from_quiet_partitions on every call;
   - activity: across all probe B records, en equals the silence (FFT-floor) values exactly when the analysed
     window has no nonzero sample.
3. Probe C, the long-window FFT stage (``expected_long_fft``):
   - downstream: every probe C line carries probe B's fields equal to ``expected_long_mapping``; the outputs for
     the inputs without selected calls were byte-identical to probe B (their recorded hashes equal B's files);
   - window: the coefficients equal float32(0.5 (1 - cos(2 PI (i - 0.5) / 1024))) with dist10's
     PI = 3.14159265358979 exactly, and differ from the periodic and the symmetric Hann window (half-sample offset);
   - framing: windowed_samples equal float32(window[j] * savebuf[j]) exactly, with savebuf rebuilt here from the
     raw input as stream samples [576k - 768, 576k + 256) (frame offset; the 1983/1984 impulses);
   - scaling and convention, against an independent float64 FFT of the captured windowed samples: pre-floor
     energy equals |X|^2 with no 1/N within 1e-6 of the call's peak, Parseval within 1e-6, phase equals angle(X)
     of the forward e^{-i 2 pi k n / N} transform within 1e-4 rad on bins above 1e-6 of the peak, and a single
     impulse gives v^2 in every bin within 1e-6 relative;
   - floor: energy differs from energy_prefloor exactly on natural bins k < 512 with float64(prefloor) <= 0.0005,
     where energy == float32(0.0005) and phi == 0. Silence floors bins 0-511 and leaves 512-1023 at 0; the quiet
     2 kHz target floors bins 169-170 inside an active window.
   The tolerances cover float32 arithmetic of the reference against a float64 FFT; they are not scientific
   thresholds.

Usage: python verify_fixture.py   (from any directory; exits non-zero if any check fails)
Re-running the reference itself needs Docker and the pinned archive; see ../README.md.
"""

import hashlib
import json
import math
import pathlib
import sys
import tempfile

FIXTURE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FIXTURE / "capture"))

import make_inputs  # noqa: E402  (sibling capture script)
import numpy as np  # noqa: E402

TRUNCATED = ("target_2k_a1024_trunc6536", "target_2k_a1024")
SILENCE = "silence_12granules"


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(folder: str, name: str) -> list[dict]:
    path = FIXTURE / folder / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def check_hashes(provenance: dict, failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        make_inputs.main(tmp)
        for raw in sorted((FIXTURE / "inputs").glob("*.raw")):
            if (pathlib.Path(tmp) / raw.name).read_bytes() != raw.read_bytes():
                failures.append(f"input {raw.name} does not regenerate byte-identically")
        if json.loads((pathlib.Path(tmp) / "inputs.json").read_text()) != json.loads(
            (FIXTURE / "inputs" / "inputs.json").read_text()
        ):
            failures.append("inputs.json differs from the regenerated manifest")
    groups = (
        ("inputs_sha256", "inputs"),
        ("expected_sha256", "expected"),
        ("expected_long_mapping_sha256", "expected_long_mapping"),
        ("expected_long_fft_sha256", "expected_long_fft"),
    )
    for group, folder in groups:
        recorded = provenance[group]
        present = {p.name for p in (FIXTURE / folder).iterdir() if p.suffix in (".raw", ".jsonl")}
        if set(recorded) != present:
            failures.append(f"{folder}: recorded {sorted(recorded)} but found {sorted(present)}")
        for name, digest in recorded.items():
            if (FIXTURE / folder / name).exists() and sha256(FIXTURE / folder / name) != digest:
                failures.append(f"{folder}/{name} SHA-256 differs from provenance.json")
    for section, key, digest_key in (
        ("probe", "patch", "patch_sha256"),
        ("probe_long_mapping", "patch", "patch_sha256"),
        ("probe_long_mapping", "harness", "harness_source_sha256"),
        ("probe_long_fft", "patch", "patch_sha256"),
        ("probe_long_fft", "harness", "harness_source_sha256"),
        ("probe_long_fft", "window_file", "window_sha256"),
    ):
        entry = provenance[section]
        if sha256(FIXTURE / entry[key]) != entry[digest_key]:
            failures.append(f"{entry[key]} SHA-256 differs from provenance.json")


def check_relations(failures: list[str]) -> None:
    for path in sorted((FIXTURE / "expected").glob("*.jsonl")):
        a, b = records("expected", path.stem), records("expected_long_mapping", path.stem)
        if [r["call"] for r in a] != [r["call"] for r in b]:
            failures.append(f"{path.stem}: probe A and B report different calls")
            continue
        for ra, rb in zip(a, b, strict=True):
            where = f"{path.stem} call {ra['call']}"
            if ra["pe"] != rb["pe"] or ra["long_path"] != rb["encoder_long_path"]:
                failures.append(f"{where}: pe or block decision differs between probes")
            if ra["long_path"] and (ra["en"] != rb["en"] or ra["thm"] != rb["thm"]):
                failures.append(f"{where}: probe B long-band values differ from the encoder path")
            if not ra["long_path"] and not all(math.isfinite(v) and v > 0 for v in rb["en"] + rb["thm"]):
                failures.append(f"{where}: probe B short-call values are not finite and positive")

    truncated, full = (records("expected_long_mapping", name) for name in TRUNCATED)
    for rt, rf in zip(truncated, full, strict=True):
        same = all(rt[k] == rf[k] for k in ("pe", "en", "thm", "thm_quiet", "thm_signal", "window_nonzero_samples"))
        if bool(rt["window_end_within_clip"]) != same:
            failures.append(f"final-frame case call {rt['call']}: equality with the full clip is {same}")

    for r in records("expected_long_mapping", SILENCE):
        if not (r["thm"] == r["thm_quiet"] == r["thm_from_quiet_partitions"]) or r["window_nonzero_samples"]:
            failures.append(f"silent masker call {r['call']}: threshold is not threshold in quiet alone")

    floor = records("expected_long_mapping", SILENCE)[0]["en"]
    for path in sorted((FIXTURE / "expected_long_mapping").glob("*.jsonl")):
        for r in records("expected_long_mapping", path.stem):
            if (r["en"] == floor) != (r["window_nonzero_samples"] == 0):
                failures.append(f"{path.stem} call {r['call']}: floor energy does not coincide with a silent window")


DIST10_PI = 3.14159265358979  # common.h l. 195
FLOOR = 0.0005  # subs.c: if (energy[i] <= 0.0005) {phi[i] = 0; energy[i] = 0.0005;}
B_FIELDS = (
    "call", "pe", "encoder_long_path", "clip_samples", "window_start", "window_end", "window_end_within_clip",
    "window_nonzero_samples", "en", "thm", "thm_quiet", "thm_from_quiet_partitions", "thm_signal",
)  # fmt: skip


def _bitrev10(k: int) -> int:
    return int(format(k, "010b")[::-1], 2)


def _savebuf(name: str, call: int) -> np.ndarray:
    """Long-FFT input of ``call`` rebuilt independently: stream samples [576k - 768, 576k + 256), zeros outside."""
    raw = np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2")
    out = np.zeros(1024, dtype=np.float32)
    for j in range(1024):
        position = 576 * call - 768 + j
        if 0 <= position < raw.size:
            out[j] = raw[position]
    return out


def _check_selected_call(name: str, r: dict, window: np.ndarray, failures: list[str]) -> None:
    where = f"{name} call {r['call']}"
    ws = np.array(r["windowed_samples"], dtype=np.float32)
    pre = np.array(r["energy_prefloor"], dtype=np.float32)
    energy = np.array(r["energy"], dtype=np.float32)
    phi = np.array(r["phi"], dtype=np.float32)
    if not np.array_equal(ws, window * _savebuf(name, r["call"])):  # float32 * float32, as FLOAT * short
        failures.append(f"{where}: windowed samples are not window * frame samples")
    spectrum = np.fft.fft(ws.astype(np.float64))  # independent float64 reference
    power = np.abs(spectrum) ** 2
    peak = power.max()
    if peak > 0:
        if np.max(np.abs(pre - power)) > 1e-6 * peak:
            failures.append(f"{where}: pre-floor energy is not the unnormalised |X|^2")
        if abs(pre.astype(np.float64).sum() / (1024 * np.sum(ws.astype(np.float64) ** 2)) - 1) > 1e-6:
            failures.append(f"{where}: Parseval (sum |X|^2 = N sum x^2) fails")
        strong = power > 1e-6 * peak
        error = np.abs((phi - np.angle(spectrum) + np.pi) % (2 * np.pi) - np.pi)
        if np.max(error[strong]) > 1e-4:
            failures.append(f"{where}: phase is not angle(X) of the forward transform")
        if np.count_nonzero(ws) == 1:
            v = float(ws[np.flatnonzero(ws)[0]])
            if np.max(np.abs(pre / (v * v) - 1)) > 1e-6:
                failures.append(f"{where}: impulse energy is not v^2 in every bin")
    natural_floor_bins = np.arange(1024) < 512
    floored = (pre.astype(np.float64) <= FLOOR) & natural_floor_bins
    if not np.array_equal(energy != pre, floored & (pre != np.float32(FLOOR))):
        failures.append(f"{where}: floor applied outside natural bins k < 512 with energy <= 0.0005")
    if not (np.all(energy[floored] == np.float32(FLOOR)) and np.all(phi[floored] == 0)):
        failures.append(f"{where}: floored bins are not energy float32(0.0005) with phase 0")


EXPECTED_FFT_EVIDENCE = {
    # (input, call): predicate on (windowed samples, pre-floor energy, energy)
    ("silence_12granules", 2): lambda ws, pre, en: (
        not ws.any() and not pre.any() and bool(np.all(en[:512] == np.float32(FLOOR))) and not en[512:].any()
    ),
    ("impulse_32767_at1983", 3): lambda ws, pre, en: np.flatnonzero(ws).tolist() == [1023],
    ("impulse_32767_at1983", 4): lambda ws, pre, en: np.flatnonzero(ws).tolist() == [447],
    ("impulse_32767_at1984", 3): lambda ws, pre, en: not ws.any(),
    ("impulse_32767_at1984", 4): lambda ws, pre, en: np.flatnonzero(ws).tolist() == [448],
    ("target_2k_a10.24", 6): lambda ws, pre, en: (
        np.count_nonzero(ws) > 0 and np.flatnonzero(en != pre).tolist() == [169, 170]
    ),
}


def check_long_fft(provenance: dict, failures: list[str]) -> None:
    entry = provenance["probe_long_fft"]
    for name, digest in entry["unselected_outputs_sha256"].items():
        if sha256(FIXTURE / "expected_long_mapping" / f"{name}.jsonl") != digest:
            failures.append(f"probe C output for {name} was not byte-identical to probe B")

    window = np.array(json.loads((FIXTURE / entry["window_file"]).read_text())["window"], dtype=np.float32)
    i = np.arange(1024)
    if not np.array_equal(window, (0.5 * (1 - np.cos(2.0 * DIST10_PI * (i - 0.5) / 1024))).astype(np.float32)):
        failures.append("window coefficients differ from float32 of the dist10 formula")
    for label, other in (
        ("periodic Hann", 0.5 * (1 - np.cos(2 * np.pi * i / 1024))),
        ("symmetric Hann", 0.5 * (1 - np.cos(2 * np.pi * i / 1023))),
    ):
        if np.array_equal(window, other.astype(np.float32)):
            failures.append(f"window coefficients are indistinguishable from the {label} window")
    # subs.c floors the even pre-reorder position i; the reorder is a 10-bit bit reversal, so in natural bin order
    # the floored set is {k : bitrev(k) even} = {k : k < 512}.
    if [_bitrev10(k) % 2 == 0 for k in range(1024)] != (i < 512).tolist():
        failures.append("bit-reversal argument for the floored bins does not hold")

    seen = {}
    for path in sorted((FIXTURE / "expected_long_fft").glob("*.jsonl")):
        name = path.stem
        captured, mapping = records("expected_long_fft", name), records("expected_long_mapping", name)
        if [{k: r[k] for k in B_FIELDS} for r in captured] != [{k: r[k] for k in B_FIELDS} for r in mapping]:
            failures.append(f"{name}: probe C changed the probe B downstream values")
        for r in captured:
            if "energy" in r:
                _check_selected_call(name, r, window, failures)
                seen[(name, r["call"])] = tuple(
                    np.array(r[key], dtype=np.float32) for key in ("windowed_samples", "energy_prefloor", "energy")
                )
    if set(seen) != {tuple(key) for key in entry["selected_calls"]}:
        failures.append(f"selected calls {sorted(seen)} differ from provenance.json")
    for key, holds in EXPECTED_FFT_EVIDENCE.items():
        if key not in seen or not holds(*seen[key]):
            failures.append(f"{key[0]} call {key[1]}: expected long-FFT evidence does not hold")


def main() -> int:
    provenance = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    check_hashes(provenance, failures)
    check_relations(failures)
    check_long_fft(provenance, failures)
    for failure in failures:
        print("FAIL:", failure)
    if not failures:
        print("fixture verified: inputs regenerate, all recorded hashes match, probe relations hold")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
