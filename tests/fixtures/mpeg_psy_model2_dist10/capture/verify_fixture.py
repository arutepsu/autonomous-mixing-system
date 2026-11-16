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
5. Probe E, partition inputs (``expected_partition``):
   - downstream: for every input, the probe E output with its fields removed hashed to the stored probe D, C or B
     file (recorded hashes equal the stored files' hashes);
   - mapping: partition_l = the 44.1 kHz long psy_data #lines repeated in partition order (465 bins), then 0 for
     the 48 unassigned bins 465..512 (static zero initialisation), so partition 0 holds DC and 465..512; numlines at
     partition time is the short table (entries 0..38, overwritten by the short-block read) then the long table;
   - inputs: partition_energy equals probe C's long energy where probe C captured the call and float32(sqrt) of
     bins 0..5 equals probe D's r_new; partition_cw equals probe D's cw;
   - accumulation: eb (double, bin order) and cb (float32 store after each double multiply-add) reconstruct exactly;
     a double-only cb and every mapping with all partition boundaries moved by one bin fail to reproduce them;
   - silence: eb[b] = #lines(b) x float32(0.0005) (partition 0: 48 floored bins, Nyquist 0); the 1983 impulse at
     call 3 (flat spectrum) gives eb[0] / eb[1] = 49 within 1e-5.
6. Probe F, spreading and tonality inputs (``expected_spreading``):
   - downstream: for every input, the probe F output without its probe E and F fields hashed to the stored probe D,
     C or B file (probe E fields were also checked equal to ``expected_partition`` at capture time);
   - matrix: s3_l[b][k] (row b = output partition, column k = input partition) has the zero pattern of the dist10
     formula on the psy_data bval (tempy <= -60), its values within 1e-15 relative (libm), and is not symmetric;
   - convolution: ecb (float32 store after every term, k ascending) and ctb (double) reconstruct exactly from the
     stored probe E eb/cb; a transposed matrix and a double-only ecb fail;
   - ratio: the division ran for every partition (ecb != 0: floored energies make every eb positive and
     s3_l[b][b] is about 1), ratio = ctb/ecb and the 0.01 lower clamp reconstruct exactly, the tonality input is
     log(clamped ratio) within 1e-15; the clamp is exercised (silence).
7. Probe G, tonality, SNR and partition thresholds (``expected_threshold``):
   - downstream: for every input, the probe G output without its probe E, F and G fields hashed to the stored probe
     D, C or B file (probe E and F fields were also checked equal to ``expected_partition``/``expected_spreading`` at
     capture time); on probe F calls the probe G ecb and tonality input equal probe F's;
   - tables: the captured minval, qthr_l and norm_l are exactly the double parse of the 44.1 kHz long psy_data
     columns (1 and 3 decimals as written there);
   - arithmetic: tbb = -0.299 - 0.43 x tonality input, its [0, 1] clamp and SNR = maximum(minval, 29 tbb + 6 (1 - tbb))
     reconstruct exactly; nb = float32(float32(ecb) x norm x exp(-SNR x 0.2302585093)) within one float32 ulp (exp is
     the only libm call); temp_1 = minimum(nb, minimum(2 nb_1, 16 nb_2)) and thr = maximum(qthr, temp_1) exactly, with
     dist10's macro tie rules;
   - history: calls are consecutive from 0; nb_1/nb_2 read are zero at call 0 and then the nb of the previous and
     the second previous call; each call stores (nb, previous nb_1), i.e. the unbounded float32 nb, not thr;
   - wrong variants fail on at least one call each: swapped history ages (2 nb_2, 16 nb_1), the final threshold stored
     in history (replayed from call 0), and the quiet floor applied before the pre-echo limit;
   - coverage: thr is set by qthr, nb, 2 nb_1 and 16 nb_2 somewhere; minval and the tonality term each decide the SNR
     somewhere; the tonality clamp acts at 0, at 1 and not at all.

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
        ("expected_partition_sha256", "expected_partition"),
        ("expected_spreading_sha256", "expected_spreading"),
        ("expected_threshold_sha256", "expected_threshold"),
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
        ("probe_partition", "patch", "patch_sha256"),
        ("probe_partition", "harness", "harness_source_sha256"),
        ("probe_partition", "mapping_file", "mapping_sha256"),
        ("probe_spreading", "patch", "patch_sha256"),
        ("probe_spreading", "harness", "harness_source_sha256"),
        ("probe_spreading", "matrix_file", "matrix_sha256"),
        ("probe_threshold", "patch", "patch_sha256"),
        ("probe_threshold", "harness", "harness_source_sha256"),
        ("probe_threshold", "tables_file", "tables_sha256"),
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


# --- probe E: partition inputs (l3psy.c ll. 553-565) ---


def accumulate_partitions(energy: np.ndarray, cw: list[float], mapping: list[int], cb_float32: bool = True):
    """eb[b] += energy[j] in double and cb[b] += cw[j] * energy[j] with a float32 store, in bin order j = 0..512."""
    eb = [0.0] * 63
    cb = np.zeros(63, dtype=np.float32 if cb_float32 else np.float64)
    for j in range(513):
        b = mapping[j]
        e = float(energy[j])
        eb[b] = eb[b] + e
        cb[b] = float(cb[b]) + cw[j] * e  # assignment into a float32 array rounds, as storing into FLOAT does
    return np.array(eb), cb


def _reference_stored_file(name: str) -> pathlib.Path:
    for folder in ("expected_short", "expected_long_fft", "expected_long_mapping"):
        if (FIXTURE / folder / f"{name}.jsonl").exists():
            return FIXTURE / folder / f"{name}.jsonl"
    raise FileNotFoundError(name)


def check_partition(provenance: dict, failures: list[str]) -> None:
    entry = provenance["probe_partition"]
    for name, digest in entry["stripped_outputs_sha256"].items():
        if sha256(_reference_stored_file(name)) != digest:
            failures.append(f"probe E output for {name} without its fields is not the stored earlier capture")
    record = json.loads((FIXTURE / entry["mapping_file"]).read_text())
    mapping, long_lines = record["partition_l"], record["psy_data_44100_long_lines"]
    short_lines = record["psy_data_44100_short_lines"]
    assigned = [b for b, n in enumerate(long_lines) for _ in range(n)]
    if (len(long_lines), sum(long_lines), len(short_lines)) != (63, 465, 39):
        failures.append("psy_data 44.1 kHz line counts are not 63 long partitions over 465 bins and 39 short ones")
    if mapping != assigned + [0] * (513 - len(assigned)):
        failures.append("partition_l is not the long #lines in order followed by partition 0 for bins 465..512")
    if [j for j, b in enumerate(mapping) if b == 0] != [0, *range(465, 513)]:
        failures.append("partition 0 does not hold exactly DC and bins 465..512")
    if record["numlines_at_partition_time"] != short_lines + long_lines[39:]:
        failures.append("numlines at partition time is not the short table over entries 0..38, then the long table")

    short = {(p.stem, r["call"]): r for p in sorted((FIXTURE / "expected_short").glob("*.jsonl")) for r in
             records("expected_short", p.stem) if "cw" in r}  # fmt: skip
    seen = {}
    for path in sorted((FIXTURE / "expected_partition").glob("*.jsonl")):
        name = path.stem
        long_fft = (
            {r["call"]: r for r in records("expected_long_fft", name) if "energy" in r}
            if (FIXTURE / "expected_long_fft" / path.name).exists()
            else {}
        )
        for r in records("expected_partition", name):
            where = f"{name} call {r['call']}"
            seen[(name, r["call"])] = r
            energy = np.array(r["partition_energy"], dtype=np.float32)
            d = short.get((name, r["call"]))
            if d is None:
                failures.append(f"{where}: probe E call is not a probe D call")
                continue
            if r["partition_cw"] != d["cw"]:
                failures.append(f"{where}: partition cw is not probe D's cw")
            if not np.array_equal(np.sqrt(energy[:6].astype(np.float64)).astype(np.float32), np.array(
                d["long_history"]["r_new"], dtype=np.float32
            )):  # fmt: skip
                failures.append(f"{where}: partition energy bins 0..5 disagree with probe D's r_new")
            if r["call"] in long_fft and not np.array_equal(
                energy, np.array(long_fft[r["call"]]["energy"][:513], dtype=np.float32)
            ):
                failures.append(f"{where}: partition energy is not probe C's long energy")
            eb, cb = accumulate_partitions(energy, r["partition_cw"], mapping)
            if eb.tolist() != r["eb"] or not np.array_equal(cb, np.array(r["cb"], dtype=np.float32)):
                failures.append(f"{where}: eb/cb do not reconstruct exactly from energy, cw and the mapping")
    if {tuple(k) for k in entry["selected_calls"]} != set(seen):
        failures.append(f"probe E selected calls {sorted(seen)} differ from provenance.json")

    # sensitivity: a double-only cb and boundary shifts by one bin must be detectable on the captured calls
    double_cb_differs = any(
        not np.array_equal(
            accumulate_partitions(np.array(r["partition_energy"], dtype=np.float32), r["partition_cw"], mapping, False)[
                1
            ].astype(np.float32),
            np.array(r["cb"], dtype=np.float32),
        )
        for r in seen.values()
    )
    if not double_cb_differs:
        failures.append("the capture cannot tell a float32 cb store from double accumulation")
    for shift in (-1, 1):
        shifted = [b for b, n in enumerate(long_lines) for _ in range(n)]
        shifted = ([0] + shifted[:-1]) if shift == 1 else (shifted[1:] + [62])
        shifted += [0] * (513 - len(shifted))
        if all(accumulate_partitions(np.array(r["partition_energy"], dtype=np.float32), r["partition_cw"], shifted)[
            0
        ].tolist() == r["eb"] for r in seen.values()):  # fmt: skip
            failures.append(f"a mapping with boundaries moved by {shift:+d} bin reproduces the captured eb")

    silence = seen.get(("silence_12granules", 2))
    floor = float(np.float32(FLOOR))
    if silence is not None:
        expected = [0.0] * 63
        for j, b in enumerate(mapping):
            expected[b] += floor if j < 512 else 0.0
        if silence["eb"] != expected:
            failures.append("silence eb is not #lines x float32(0.0005) per partition (Nyquist 0)")
    impulse = seen.get(("impulse_32767_at1983", 3))
    if impulse is None or abs(impulse["eb"][0] / impulse["eb"][1] - 49) > 49e-5:
        failures.append("the flat 1983 impulse spectrum does not show 49 bins in partition 0")


# --- probe F: spreading and tonality inputs (l3psy.c ll. 573-604, L3para_read ll. 819-838) ---
LN_TO_LOG10 = 0.2302585093  # common.h l. 199 (truncated)


def dist10_spreading_matrix(bval: list[float]) -> np.ndarray:
    """s3_l[i][j] from L3para_read: i = output (centre) partition, j = input partition, in double."""
    s3 = np.zeros((len(bval), len(bval)))
    for i in range(len(bval)):
        for j in range(len(bval)):
            tempx = (bval[i] - bval[j]) * (3.0 if j >= i else 1.5)
            x = 8.0 * ((tempx - 0.5) ** 2 - 2.0 * (tempx - 0.5)) if 0.5 <= tempx <= 2.5 else 0.0
            tempx += 0.474
            tempy = 15.811389 + 7.5 * tempx - 17.5 * math.sqrt(1.0 + tempx * tempx)
            s3[i, j] = 0.0 if tempy <= -60.0 else math.exp((x + tempy) * LN_TO_LOG10)
    return s3


def spread_partitions(eb, cb, s3: np.ndarray, ecb_float32: bool = True):
    """ecb[b] += s3[b][k] * eb[k] (float32 store per term) and ctb[b] += s3[b][k] * cb[k] (double), k ascending."""
    ecb = np.zeros(63, dtype=np.float32 if ecb_float32 else np.float64)
    ctb = [0.0] * 63
    for b in range(63):
        for k in range(63):
            ecb[b] = float(ecb[b]) + float(s3[b, k]) * float(eb[k])
            ctb[b] = ctb[b] + float(s3[b, k]) * float(cb[k])
    return ecb, ctb


def check_spreading(provenance: dict, failures: list[str]) -> None:
    entry = provenance["probe_spreading"]
    for name, digest in entry["stripped_outputs_sha256"].items():
        if sha256(_reference_stored_file(name)) != digest:
            failures.append(f"probe F output for {name} without its fields is not the stored earlier capture")
    record = json.loads((FIXTURE / entry["matrix_file"]).read_text())
    s3 = np.array(record["s3_l"])
    formula = dist10_spreading_matrix([float(v) for v in record["psy_data_44100_long_bval"]])
    if s3.shape != (63, 63) or not np.array_equal(s3 == 0, formula == 0):
        failures.append("s3_l does not have the dist10 formula's shape and zero pattern (tempy <= -60)")
    nonzero = formula != 0
    if np.max(np.abs(s3[nonzero] - formula[nonzero]) / formula[nonzero]) > 1e-15:
        failures.append("s3_l values differ from the dist10 formula on psy_data bval beyond libm rounding")
    if np.array_equal(s3, s3.T):
        failures.append("s3_l is symmetric, so a transposed matrix could not be detected")

    partition = {
        (p.stem, r["call"]): r
        for p in sorted((FIXTURE / "expected_partition").glob("*.jsonl"))
        for r in records("expected_partition", p.stem)
    }
    seen, transposed_detected, double_detected, clamp_seen = {}, 0, 0, False
    for path in sorted((FIXTURE / "expected_spreading").glob("*.jsonl")):
        for r in records("expected_spreading", path.stem):
            key = (path.stem, r["call"])
            where = f"{key[0]} call {key[1]}"
            seen[key] = r
            e = partition.get(key)
            if e is None:
                failures.append(f"{where}: probe F call is not a probe E call")
                continue
            cb = np.array(e["cb"], dtype=np.float32)
            ecb, ctb = spread_partitions(e["eb"], cb, s3)
            if not np.array_equal(ecb, np.array(r["ecb"], dtype=np.float32)) or ctb != r["ctb"]:
                failures.append(f"{where}: ecb/ctb do not reconstruct exactly from eb, cb and s3_l")
            transposed_detected += not np.array_equal(spread_partitions(e["eb"], cb, s3.T)[0], ecb)
            double_ecb = spread_partitions(e["eb"], cb, s3, ecb_float32=False)[0].astype(np.float32)
            double_detected += not np.array_equal(double_ecb, ecb)
            if r["ratio_divided"] != [1] * 63 or not all(v != 0 for v in r["ecb"]):
                failures.append(f"{where}: the division was skipped somewhere; its branch needs its own evidence")
            ratio = [ctb[b] / float(ecb[b]) for b in range(63)]
            clamped = [0.01 if v < 0.01 else v for v in ratio]  # if (cbb < 0.01) cbb = 0.01;
            if ratio != r["ratio"] or clamped != r["ratio_clamped"]:
                failures.append(f"{where}: ratio or its 0.01 clamp does not reconstruct exactly")
            if max(abs(math.log(c) - t) for c, t in zip(clamped, r["tonality_input"], strict=True)) > 1e-15:
                failures.append(f"{where}: tonality input is not log of the clamped ratio")
            clamp_seen |= any(v < 0.01 for v in ratio)
    if {tuple(k) for k in entry["selected_calls"]} != set(seen):
        failures.append(f"probe F selected calls {sorted(seen)} differ from provenance.json")
    if transposed_detected != len(seen):
        failures.append(f"a transposed s3_l reproduces ecb on {len(seen) - transposed_detected} calls")
    if not double_detected:
        failures.append("the capture cannot tell a float32 ecb store from double accumulation")
    if not clamp_seen:
        failures.append("the 0.01 lower clamp is never exercised")


# --- probe G: tonality, SNR and partition thresholds (l3psy.c ll. 591-617) ---
def _maximum(x: float, y: float) -> float:
    return x if x > y else y  # l3psy.c l. 33: #define maximum(x,y) ( (x>y) ? x : y )


def _minimum(x: float, y: float) -> float:
    return x if x < y else y  # l3psy.c l. 34: #define minimum(x,y) ( (x<y) ? x : y )


def threshold_terms(ecb, tonality_input, tables: dict) -> dict:
    """Per partition, from the spread energy and the value passed to tbb: raw and clamped tonality, SNR and nb."""
    raw, tbb, snr, nb = [], [], [], []
    for b in range(63):
        t = -0.299 - 0.43 * tonality_input[b]  # conv1, conv2
        raw.append(t)
        t = _minimum(1.0, _maximum(0.0, t))
        tbb.append(t)
        s = _maximum(tables["minval"][b], 29.0 * t + 6.0 * (1.0 - t))  # TMN = 29, NMT = 6 dB
        snr.append(s)
        nb.append(float(np.float32(float(np.float32(ecb[b])) * tables["norm_l"][b] * math.exp(-s * LN_TO_LOG10))))
    return {"tonality_raw": raw, "tonality": tbb, "snr": snr, "nb": nb}


def pre_echo_threshold(nb, nb_1, nb_2, qthr, swap_ages: bool = False, floor_first: bool = False):
    """temp_1 = minimum(nb, minimum(2 nb_1, 16 nb_2)); thr = maximum(qthr, temp_1). The flags are wrong variants."""
    limit, thr = [], []
    for b in range(63):
        older = (2.0 * nb_2[b], 16.0 * nb_1[b]) if swap_ages else (2.0 * nb_1[b], 16.0 * nb_2[b])
        if floor_first:
            temp_1 = _minimum(_maximum(qthr[b], nb[b]), _minimum(*older))
            limit.append(temp_1)
            thr.append(temp_1)
        else:
            temp_1 = _minimum(nb[b], _minimum(*older))
            limit.append(temp_1)
            thr.append(_maximum(qthr[b], temp_1))
    return limit, thr


def limiting_term(r: dict, qthr: list[float], b: int) -> str:
    """Which term sets thr[b]: qthr, nb, 2 nb_1 or 16 nb_2 (strict winners; ties are reported as such)."""
    nb, two, sixteen = float(np.float32(r["nb"][b])), 2.0 * r["nb_1_before"][b], 16.0 * r["nb_2_before"][b]
    limit = r["pre_echo_limit"][b]
    if qthr[b] > limit:
        return "qthr"
    if qthr[b] == limit:
        return "tie qthr/limit"
    hits = [name for name, v in (("nb", nb), ("2nb_1", two), ("16nb_2", sixteen)) if v == limit]
    return hits[0] if len(hits) == 1 else "tie " + "/".join(hits)


def check_threshold(provenance: dict, failures: list[str]) -> None:
    entry = provenance["probe_threshold"]
    for name, digest in entry["stripped_outputs_sha256"].items():
        if sha256(_reference_stored_file(name)) != digest:
            failures.append(f"probe G output for {name} without its fields is not the stored earlier capture")
    tables = json.loads((FIXTURE / entry["tables_file"]).read_text())
    for key, text in (("minval", "minval"), ("qthr_l", "qthr"), ("norm_l", "norm")):
        if tables[key] != [float(s) for s in tables[f"psy_data_44100_long_{text}"]] or len(tables[key]) != 63:
            failures.append(f"captured {key} is not the double parse of the 44.1 kHz long psy_data {text} column")
    qthr = tables["qthr_l"]
    spreading = {
        (p.stem, r["call"]): r
        for p in sorted((FIXTURE / "expected_spreading").glob("*.jsonl"))
        for r in records("expected_spreading", p.stem)
    }
    seen, terms, snr_terms, clamp = set(), set(), set(), set()
    detected = {"swapped history ages": 0, "final threshold stored in history": 0, "quiet floor before limit": 0}
    zero_start = 0
    for path in sorted((FIXTURE / "expected_threshold").glob("*.jsonl")):
        calls = records("expected_threshold", path.stem)
        if [r["call"] for r in calls] != list(range(len(calls))):
            failures.append(f"{path.stem}: probe G calls are not consecutive from call 0")
            continue
        replay = {"nb": ([0.0] * 63, [0.0] * 63), "thr": ([0.0] * 63, [0.0] * 63)}  # static zero initialisation
        for r in calls:
            key = (path.stem, r["call"])
            where = f"{key[0]} call {key[1]}"
            seen.add(key)
            f = spreading.get(key)
            if f is not None and (r["ecb"] != f["ecb"] or r["tonality_input"] != f["tonality_input"]):
                failures.append(f"{where}: probe G ecb / tonality input differ from probe F")
            got = threshold_terms(r["ecb"], r["tonality_input"], tables)
            for field in ("tonality_raw", "tonality", "snr"):
                if got[field] != r[field]:
                    failures.append(f"{where}: {field} does not reconstruct exactly")
            nb = [float(np.float32(v)) for v in r["nb"]]
            ulp = [abs(g - n) / float(np.spacing(np.float32(n))) for g, n in zip(got["nb"], nb, strict=True)]
            if max(ulp) > 1:  # float32 store of a double product; the only libm call is exp
                failures.append(f"{where}: nb differs from float32(ecb norm exp(-SNR LN_TO_LOG10)) by > 1 ulp")
            n1, n2 = r["nb_1_before"], r["nb_2_before"]
            if r["call"] == 0:
                zero_start += n1 == [0.0] * 63 and n2 == [0.0] * 63
            if (n1, n2) != replay["nb"]:
                failures.append(f"{where}: history read is not (nb of call k-1, nb of call k-2), zero before call 0")
            if r["nb_1_after"] != nb or r["nb_2_after"] != n1:
                failures.append(f"{where}: the call does not store (nb, previous nb_1) as its history")
            limit, thr = pre_echo_threshold(nb, n1, n2, qthr)
            if limit != r["pre_echo_limit"] or thr != r["thr"]:
                failures.append(f"{where}: pre-echo limit or final threshold does not reconstruct exactly")
            # wrong variants, each replayed from call 0 with its own history where the history is what changes
            detected["swapped history ages"] += pre_echo_threshold(nb, n1, n2, qthr, swap_ages=True)[1] != r["thr"]
            wrong = pre_echo_threshold(nb, *replay["thr"], qthr)[1]
            detected["final threshold stored in history"] += wrong != r["thr"]
            detected["quiet floor before limit"] += (
                pre_echo_threshold(nb, n1, n2, qthr, floor_first=True)[1] != r["thr"]
            )
            replay["nb"] = (nb, n1)
            replay["thr"] = (wrong, replay["thr"][0])
            for b in range(63):
                terms.add(limiting_term(r, qthr, b))
                tonal = 29.0 * r["tonality"][b] + 6.0 * (1.0 - r["tonality"][b])
                snr_terms.add(
                    "minval" if tables["minval"][b] > tonal else "tie" if tables["minval"][b] == tonal else "tonal"
                )
                raw = r["tonality_raw"][b]
                clamp.add("0" if raw < 0.0 else "1" if raw > 1.0 else "interior")
    if {tuple(k) for k in entry["selected_calls"]} != seen:
        failures.append(f"probe G selected calls {sorted(seen)} differ from provenance.json")
    if not zero_start:
        failures.append("no probe G input starts from zero history")
    for label, count in detected.items():
        if not count:
            failures.append(f"the capture cannot detect {label}")
    for needed in ("qthr", "nb", "2nb_1", "16nb_2"):
        if needed not in terms:
            failures.append(f"no partition threshold is set by {needed}; that branch needs its own evidence")
    if not {"minval", "tonal"} <= snr_terms:
        failures.append("the SNR floor minval or the tonality term never decides the SNR")
    if clamp != {"0", "1", "interior"}:
        failures.append(f"the tonality clamp is not exercised at both ends and inside (seen {sorted(clamp)})")


def main() -> int:
    provenance = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    check_hashes(provenance, failures)
    check_relations(failures)
    check_long_fft(provenance, failures)
    check_short(provenance, failures)
    check_partition(provenance, failures)
    check_spreading(provenance, failures)
    check_threshold(provenance, failures)
    for failure in failures:
        print("FAIL:", failure)
    if not failures:
        print("fixture verified: inputs regenerate, all recorded hashes match, probe relations hold")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
