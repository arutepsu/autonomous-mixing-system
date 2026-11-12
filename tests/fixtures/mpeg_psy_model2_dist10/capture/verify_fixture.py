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
4. Probe D, short FFTs and unpredictability (``expected_short``):
   - downstream: with probe D fields removed, every line equals the stored probe C (or B) line, and the inputs
     without probe D calls gave output byte-identical to the stored probe C / B files;
   - short window: float32 of the dist10 formula over 256 points, exactly;
   - offsets: block s holds window_s[j] * savebuf[128 (2 + s) + j], i.e. stream samples [576k - 512 + 128 s, +256),
     exactly; the 1983 impulse sits at positions 191 and 63 of call 4's blocks 0 and 1 and in no block of call 3;
   - short FFT: pre-floor energy |X|^2 within 1e-6 of the block peak, phase angle(X) within 1e-4 rad on strong
     unfloored bins; floor exactly on short bins k < 128 with energy <= 0.0005 (float32(0.0005), phase 0);
   - history: age indices alternate (new/old/oldest = 1/0/1 on even calls, 0/1/0 on odd), r/phi_sav ``old`` and
     ``oldest`` equal the values stored by the previous and the second previous call, zero before call 0;
   - cw: reconstructed in double from the captured float32 inputs within 1e-12, with lines 6..205 from short bin
     (j + 2) / 4 predicting block 1 from 2 r(block 0) - r(block 2), constant in groups of four, and 0.4 from line
     206 on; silence gives cw[0..5] = 1, 1/3, 0 at calls 0, 1, 2 (zero, one-call and established history);
   - where probe C captured the same call, r_new = float32(sqrt(long energy[0..5])) and phi_new = long phase[0..5];
   - the zero-denominator branch (cw = 0) is never taken: every denominator met is positive;
   - partially floored active short block (provenance ``partial_floor_case``, quiet 2 kHz target call 6 block 2):
     exactly short bins 42 and 86 floored, to float32(0.0005) with phase 0; every other bin, Nyquist 128 included,
     keeps its pre-floor energy; floored energies <= 0.5 x 0.0005 and unfloored >= 2 x 0.0005.

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
        ("expected_short_sha256", "expected_short"),
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
        ("probe_short", "patch", "patch_sha256"),
        ("probe_short", "harness", "harness_source_sha256"),
        ("probe_short", "window_file", "window_sha256"),
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


# --- probe D: short FFTs and unpredictability (rules as traced in l3psy.c; see the contract, section 3.2) ---
D_FIELDS = ("short_windowed_samples", "short_energy_prefloor", "short_energy", "short_phi", "long_history", "cw")
SHORT_BINS = 129  # natural short bins 0..128 (HBLKSIZE_s)


def short_block_offset(block: int) -> int:
    """Position in savebuf of short block ``block``'s first sample (l3psy.c: k = 128 * (2 + sblock))."""
    return 128 * (2 + block)


def short_bin_for_line(line: int) -> int:
    """Short-FFT bin used for unpredictability lines line..line+3, line = 6, 10, ..., 202 (k = (j + 2) / 4)."""
    return (line + 2) // 4


def long_history_calls(call: int) -> tuple[int, int]:
    """Calls whose stored r/phi_sav are read as ``old`` and ``oldest`` by call ``call`` (negative: zero init)."""
    return call - 1, call - 2


SHORT_PREDICTOR = (0, 2, 1)  # r' = 2 r(block 0) - r(block 2), compared with block 1 (l3psy.c ll. 521-536)


def _short_frame(name: str, call: int, block: int) -> np.ndarray:
    raw = np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2")
    out = np.zeros(256, dtype=np.float32)
    for n in range(256):
        position = 576 * call - 768 + short_block_offset(block) + n
        if 0 <= position < raw.size:
            out[n] = raw[position]
    return out


def _unpredictability(r_new, phi_new, r_prime, phi_prime) -> float:
    t1 = r_new * math.cos(phi_new) - r_prime * math.cos(phi_prime)
    t2 = r_new * math.sin(phi_new) - r_prime * math.sin(phi_prime)
    t3 = r_new + abs(r_prime)
    return math.sqrt(t1 * t1 + t2 * t2) / t3 if t3 != 0 else 0.0  # the reference's zero-denominator branch


def reconstruct_cw(record: dict, history: dict[int, dict]) -> tuple[np.ndarray, float]:
    """cw[0..512] from the captured float32 inputs in double, and the smallest denominator met."""
    cw = np.full(513, 0.4)
    smallest = math.inf
    h = record["long_history"]
    old_call, oldest_call = long_history_calls(record["call"])

    def previous(call: int, stored_key: str, own_key: str) -> list[float]:
        """Stored values of ``call``: zero before the first call, the captured call's own copy when the run holds
        it, otherwise this record's captured history (the run's first selected call has no captured source)."""
        if call < 0:
            return [0.0] * 6
        values = history[call]["long_history"][stored_key] if call in history else h[own_key]
        return [float(np.float32(v)) for v in values]

    r_old, p_old = previous(old_call, "r_new", "r_old"), previous(old_call, "phi_new", "phi_old")
    r_oldest, p_oldest = previous(oldest_call, "r_new", "r_oldest"), previous(oldest_call, "phi_new", "phi_oldest")
    for b in range(6):
        r_new, phi_new = float(np.float32(h["r_new"][b])), float(np.float32(h["phi_new"][b]))
        r_prime, phi_prime = 2.0 * r_old[b] - r_oldest[b], 2.0 * p_old[b] - p_oldest[b]
        smallest = min(smallest, r_new + abs(r_prime))
        cw[b] = _unpredictability(r_new, phi_new, r_prime, phi_prime)
    energy = [[float(np.float32(v)) for v in row] for row in record["short_energy"]]
    phase = [[float(np.float32(v)) for v in row] for row in record["short_phi"]]
    first, second, target = SHORT_PREDICTOR
    for line in range(6, 206, 4):
        k = short_bin_for_line(line)
        r_prime = 2.0 * math.sqrt(energy[first][k]) - math.sqrt(energy[second][k])
        phi_prime = 2.0 * phase[first][k] - phase[second][k]
        r2 = math.sqrt(energy[target][k])
        smallest = min(smallest, r2 + abs(r_prime))
        cw[line : line + 4] = _unpredictability(r2, phase[target][k], r_prime, phi_prime)
    return cw, smallest


def _check_short_call(name: str, r: dict, window_s: np.ndarray, history: dict, failures: list[str]) -> float:
    where = f"{name} call {r['call']}"
    wsamp = np.array(r["short_windowed_samples"], dtype=np.float32)
    pre = np.array(r["short_energy_prefloor"], dtype=np.float32)
    energy = np.array(r["short_energy"], dtype=np.float32)
    phi = np.array(r["short_phi"], dtype=np.float32)
    bins = np.arange(SHORT_BINS)
    for block in range(3):
        if not np.array_equal(wsamp[block], window_s * _short_frame(name, r["call"], block)):
            failures.append(f"{where} block {block}: short windowed samples are not window_s * savebuf[offset + j]")
        spectrum = np.fft.fft(wsamp[block].astype(np.float64))[:SHORT_BINS]
        power = np.abs(spectrum) ** 2
        peak = power.max()
        if peak > 0:
            if np.max(np.abs(pre[block] - power)) > 1e-6 * peak:
                failures.append(f"{where} block {block}: short pre-floor energy is not the unnormalised |X|^2")
            strong = power > 1e-6 * peak
            error = np.abs((phi[block] - np.angle(spectrum) + np.pi) % (2 * np.pi) - np.pi)
            if np.max(error[strong & (energy[block] == pre[block])]) > 1e-4:
                failures.append(f"{where} block {block}: short phase is not angle(X)")
        floored = (pre[block].astype(np.float64) <= FLOOR) & (bins < 128)
        if not np.array_equal(energy[block] != pre[block], floored & (pre[block] != np.float32(FLOOR))):
            failures.append(f"{where} block {block}: short floor applied outside bins k < 128 with energy <= 0.0005")
        if not (np.all(energy[block][floored] == np.float32(FLOOR)) and np.all(phi[block][floored] == 0)):
            failures.append(f"{where} block {block}: floored short bins are not float32(0.0005) with phase 0")
    h = r["long_history"]
    parity = r["call"] % 2
    if (h["age_new"], h["age_old"], h["age_oldest"]) != (1 - parity, parity, 1 - parity):
        failures.append(f"{where}: age indices are not the alternating new/old/oldest = {1 - parity}/{parity}/...")
    for key, source_call, source_key in (
        ("r_old", long_history_calls(r["call"])[0], "r_new"),
        ("r_oldest", long_history_calls(r["call"])[1], "r_new"),
        ("phi_old", long_history_calls(r["call"])[0], "phi_new"),
        ("phi_oldest", long_history_calls(r["call"])[1], "phi_new"),
    ):
        if source_call < 0:
            if any(h[key]):
                failures.append(f"{where}: {key} is not the zero initial history")
        elif source_call in history and h[key] != history[source_call]["long_history"][source_key]:
            failures.append(f"{where}: {key} is not call {source_call}'s stored {source_key}")
    cw = np.array(r["cw"])
    reconstructed, smallest = reconstruct_cw(r, history)
    if np.max(np.abs(reconstructed - cw)) > 1e-12:
        failures.append(f"{where}: cw differs from the reconstruction (history, bin mapping or block order)")
    if not np.all(cw[206:] == 0.4):
        failures.append(f"{where}: cw[206..512] is not the constant 0.4")
    if any(len(set(cw[line : line + 4].tolist())) != 1 for line in range(6, 206, 4)):
        failures.append(f"{where}: cw lines 6..205 are not constant in groups of four")
    return smallest


EXPECTED_SHORT_EVIDENCE = {
    # (input, call): predicate on the record; zero-history sequence on silence, offsets on the impulse
    ("silence_12granules", 0): lambda r: np.allclose(r["cw"][:6], 1.0, atol=1e-12) and not any(r["cw"][6:206]),
    ("silence_12granules", 1): lambda r: np.allclose(r["cw"][:6], 1 / 3, atol=1e-12),
    ("silence_12granules", 2): lambda r: np.allclose(r["cw"][:6], 0.0, atol=1e-12),
    ("impulse_32767_at1983", 3): lambda r: (
        not np.any(r["short_windowed_samples"]) and min(r["cw"][:6]) > 0.9
    ),  # the long window holds sample 1983 at position 1023, no short block does
    ("impulse_32767_at1983", 4): lambda r: (
        [np.flatnonzero(b).tolist() for b in r["short_windowed_samples"]] == [[191], [63], []]
    ),  # sample 1983 at positions 191 and 63 of blocks 0 and 1; block 2 starts at 2048
    ("masker_1k_a8192", 0): lambda r: np.allclose(r["cw"][:6], 1.0, atol=1e-12) and not any(r["cw"][6:206]),
}


def check_short(provenance: dict, failures: list[str]) -> None:
    entry = provenance["probe_short"]
    for name, digest in entry["unselected_outputs_sha256"].items():
        folder = (
            "expected_long_fft"
            if (FIXTURE / "expected_long_fft" / f"{name}.jsonl").exists()
            else "expected_long_mapping"
        )
        if sha256(FIXTURE / folder / f"{name}.jsonl") != digest:
            failures.append(f"probe D output for {name} was not byte-identical to {folder}")
    window_s = np.array(json.loads((FIXTURE / entry["window_file"]).read_text())["window_s"], dtype=np.float32)
    j = np.arange(256)
    if not np.array_equal(window_s, (0.5 * (1 - np.cos(2.0 * DIST10_PI * (j - 0.5) / 256))).astype(np.float32)):
        failures.append("short window coefficients differ from float32 of the dist10 formula")
    if [int(format(k, "08b")[::-1], 2) % 2 == 0 for k in range(256)] != (j < 128).tolist():
        failures.append("bit-reversal argument for the floored short bins does not hold")

    seen, smallest = {}, math.inf
    for path in sorted((FIXTURE / "expected_short").glob("*.jsonl")):
        name = path.stem
        folder = (
            "expected_long_fft" if (FIXTURE / "expected_long_fft" / path.name).exists() else "expected_long_mapping"
        )
        captured = records("expected_short", name)
        stripped = [{k: v for k, v in r.items() if k not in D_FIELDS} for r in captured]
        if stripped != records(folder, name):
            failures.append(f"{name}: probe D changed the {folder} values")
        history = {r["call"]: r for r in captured if "cw" in r}
        for call, r in history.items():
            smallest = min(smallest, _check_short_call(name, r, window_s, history, failures))
            seen[(name, call)] = r
            if "energy" in r:  # the same call also carries probe C's long-FFT arrays
                long_energy = np.array(r["energy"][:6], dtype=np.float32).astype(np.float64)
                stored_r = np.array(r["long_history"]["r_new"], dtype=np.float32)
                if not np.array_equal(stored_r, np.sqrt(long_energy).astype(np.float32)):
                    failures.append(f"{name} call {call}: r_new is not float32(sqrt(long energy[0..5]))")
                if r["long_history"]["phi_new"] != r["phi"][:6]:
                    failures.append(f"{name} call {call}: phi_new is not the long FFT phase[0..5]")
    if set(seen) != {tuple(key) for key in entry["selected_calls"]}:
        failures.append(f"probe D selected calls {sorted(seen)} differ from provenance.json")
    if not smallest > 0:
        failures.append("a zero denominator occurred; its cw = 0 branch needs its own evidence")
    for key, holds in EXPECTED_SHORT_EVIDENCE.items():
        if key not in seen or not holds(seen[key]):
            failures.append(f"{key[0]} call {key[1]}: expected short-FFT evidence does not hold")
    _check_partial_short_floor(entry["partial_floor_case"], seen, failures)


def _check_partial_short_floor(case: dict, seen: dict, failures: list[str]) -> None:
    """The recorded partially floored active short block: exact floored set, values and margins from the cutoff."""
    where = f"partial-floor case {case['input']} call {case['call']} block {case['block']}"
    record = seen.get((case["input"], case["call"]))
    if record is None:
        failures.append(f"{where}: call not captured")
        return
    pre = np.array(record["short_energy_prefloor"][case["block"]], dtype=np.float32)
    energy = np.array(record["short_energy"][case["block"]], dtype=np.float32)
    phi = np.array(record["short_phi"][case["block"]], dtype=np.float32)
    floored = sorted(case["floored_short_bins"])
    predicted = np.flatnonzero((pre[:128].astype(np.float64) <= FLOOR)).tolist()
    if predicted != floored or not 0 < len(floored) < 128:
        failures.append(f"{where}: floored bins {predicted}, recorded {floored} (must be a proper nonempty subset)")
    if not np.count_nonzero(record["short_windowed_samples"][case["block"]]):
        failures.append(f"{where}: block is not active")
    others = np.setdiff1d(np.arange(SHORT_BINS), floored)
    if not (np.all(energy[floored] == np.float32(FLOOR)) and np.all(phi[floored] == 0)):
        failures.append(f"{where}: floored bins are not float32(0.0005) with phase 0")
    if not np.array_equal(energy[others], pre[others]):
        failures.append(f"{where}: unfloored bins (including Nyquist 128) do not keep their pre-floor energy")
    if np.all(phi[others[others < 128]] == 0):
        failures.append(f"{where}: unfloored bins carry no phase")
    # clearly on both sides of the cutoff: float32 rounding (relative 6e-8) cannot move a bin across 0.0005
    if not (pre[floored].astype(np.float64).max() <= 0.5 * FLOOR and pre[others[others < 128]].min() >= 2 * FLOOR):
        failures.append(f"{where}: energies are not clearly on both sides of 0.0005")
    if not pre[128] > FLOOR or energy[128] != pre[128]:
        failures.append(f"{where}: Nyquist bin 128 was altered or is not an active bin")


def main() -> int:
    provenance = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    check_hashes(provenance, failures)
    check_relations(failures)
    check_long_fft(provenance, failures)
    check_short(provenance, failures)
    for failure in failures:
        print("FAIL:", failure)
    if not failures:
        print("fixture verified: inputs regenerate, all recorded hashes match, probe relations hold")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
