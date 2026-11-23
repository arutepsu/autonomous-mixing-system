"""Fixed P1 numerical engineering controls; no music, provider, diagnosis or qualification policy.

Two phases: prepare freezes exact numerical input bytes; run verifies them before DSP. The registered research
run is separately authorized. Unit tests use temporary numerical fixtures. This script is not a runtime adapter.
"""

import argparse
import hashlib
import json
import math
import os
import platform
import re
import struct
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests/fixtures/mpeg_psy_model2_dist10"
RATE, LENGTH = 44100, 6912
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
MAX_SECONDS = 300.0
VARIANTS = ("nominal", "A-plus", "A-minus", "B-plus", "B-minus")
BOUNDARY = {"max_midpoints": 15, "phases": [0.0, math.pi / 2], "low": 1, "high": 32767, "call": 6}
RECIPE = {
    "version": "p1-small-controls@1",
    "rate": RATE,
    "length": LENGTH,
    "amplitudes": [1, 4, 16, 256, 1024],
    "phases": [0.0, math.pi / 2],
    "taus": [0.02, 0.1],
    "variants": list(VARIANTS),
    "boundary": BOUNDARY,
    "rounding": "Python binary64 scalar math; round half to even; no clipping",
    "qualification": "unavailable; no numerical policy selected",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def quantize(values, offset=0.0):
    counts = []
    for value in values:
        value = float(value) + offset
        if not math.isfinite(value):
            raise ValueError("nonfinite sample")
        count = round(value)
        if not -32768 <= count <= 32767:
            raise ValueError("sample outside int16; clipping refused")
        counts.append(count)
    return struct.pack(f"<{len(counts)}h", *counts)


def tone(amplitude, frequency, phase=0.0):
    return [amplitude * math.sin(2 * math.pi * frequency * n / RATE + phase) for n in range(LENGTH)]


def control_pairs():
    rows = []
    bases = []
    for a in RECIPE["amplitudes"]:
        for phase_index, phi in enumerate(RECIPE["phases"]):
            bases.append((f"tone-{a}-{phase_index}", tone(8192, 1000), tone(a, 2000, phi)))
    for tau in RECIPE["taus"]:
        decay = [
            0.0
            if n < 2000
            else 8192 * math.exp(-((n - 2000) / RATE) / tau) * math.sin(2 * math.pi * 1000 * (n - 2000) / RATE)
            for n in range(LENGTH)
        ]
        bases.append((f"decay-{tau}", decay, tone(1024, 2000)))
    for name, a, b in bases:
        nominal_a, nominal_b = quantize(a), quantize(b)
        for variant in VARIANTS:
            aa = quantize(a, 0.5 if variant == "A-plus" else -0.5) if variant.startswith("A-") else nominal_a
            bb = quantize(b, 0.5 if variant == "B-plus" else -0.5) if variant.startswith("B-") else nominal_b
            for actual, nominal in ((aa, nominal_a), (bb, nominal_b)):
                if (
                    np.max(
                        np.abs(
                            np.frombuffer(actual, dtype="<i2").astype(int)
                            - np.frombuffer(nominal, dtype="<i2").astype(int)
                        )
                    )
                    > 1
                ):
                    raise ValueError("perturbation exceeds one count")
            rows.append(
                {
                    "id": f"{name}-{variant}",
                    "family": "Q",
                    "variant": variant,
                    "nominal": f"{name}-nominal",
                    "A": aa,
                    "B": bb,
                }
            )
    a, b = quantize(tone(8192, 1000)), quantize(tone(1024, 2000))
    zeros = bytes(LENGTH * 2)
    for variant, aa, bb in (
        ("nominal", a, b),
        ("A-zero", zeros, b),
        ("B-zero", a, zeros),
        ("A-tail", a[: 3456 * 2] + zeros[3456 * 2 :], b),
        ("B-tail", a, b[: 3456 * 2] + zeros[3456 * 2 :]),
    ):
        rows.append(
            {"id": f"zero-{variant}", "family": "Z", "variant": variant, "nominal": "zero-nominal", "A": aa, "B": bb}
        )
    return rows


def fingerprint():
    paths = [Path(__file__).resolve(), REPO / "uv.lock", REPO / "scripts/fl_studio/analyze_trial.py"]
    paths += sorted((REPO / "src").rglob("*.py"))
    paths += sorted(
        p for p in FIXTURE.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )
    return {
        "files": {p.relative_to(REPO).as_posix(): digest(p.read_bytes()) for p in paths},
        "environment": {"python": sys.version, "numpy": np.__version__, "platform": platform.platform()},
    }


def write_exclusive(path, data):
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def read_bounded(path, maximum):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"not a regular prepared file: {path.name}")
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("prepared file exceeds byte limit")
    return data


def prepare(out, *, cancelled=None):
    out = Path(out)
    if out.resolve().is_relative_to(REPO):
        raise ValueError("research output must be outside the repository")
    out.mkdir(parents=True, exist_ok=False)
    inputs = out / "inputs"
    inputs.mkdir()
    budget = Budget(out, cancelled=cancelled)
    budget.check()
    rows, seen, total = [], set(), 0
    for pair in control_pairs():
        budget.check()
        row = {k: v for k, v in pair.items() if k not in ("A", "B")}
        for role in ("A", "B"):
            raw = pair[role]
            h = digest(raw)
            if h not in seen:
                if total + len(raw) > MAX_INPUT_BYTES:
                    raise ValueError("input byte limit exceeded")
                write_exclusive(inputs / f"{h}.raw", raw)
                total += len(raw)
                seen.add(h)
            row[role] = h
        rows.append(row)
    manifest = {
        "schema": 1,
        "recipe": RECIPE,
        "boundary": BOUNDARY,
        "pairs": rows,
        "fingerprint": fingerprint(),
        "input_bytes": total,
    }
    payload = canonical(manifest)
    write_exclusive(out / "control-inputs.json", payload)
    budget.check()
    write_exclusive(out / "control-inputs.sha256", digest(payload).encode("ascii"))
    return manifest


def load_prepared(out):
    out = Path(out)
    if out.is_symlink() or (out / "inputs").is_symlink():
        raise ValueError("linked research directory refused")
    payload = read_bounded(out / "control-inputs.json", 1 << 20)
    if digest(payload).encode("ascii") != read_bounded(out / "control-inputs.sha256", 64):
        raise ValueError("manifest hash differs")
    manifest = json.loads(payload)
    if canonical(manifest) != payload or manifest["schema"] != 1 or manifest["recipe"] != RECIPE:
        raise ValueError("manifest recipe differs")
    if manifest["boundary"] != BOUNDARY or manifest["fingerprint"] != fingerprint():
        raise ValueError("source or environment fingerprint differs")
    pairs = manifest["pairs"]
    expected_ids = [f"tone-{a}-{i}-{v}" for a in RECIPE["amplitudes"] for i in range(2) for v in VARIANTS]
    expected_ids += [f"decay-{tau}-{v}" for tau in RECIPE["taus"] for v in VARIANTS]
    expected_ids += [f"zero-{v}" for v in ("nominal", "A-zero", "B-zero", "A-tail", "B-tail")]
    if [r["id"] for r in pairs] != expected_ids:
        raise ValueError("manifest pair order differs")
    total, seen = 0, set()
    for row in pairs:
        expected_family = "Z" if row["id"].startswith("zero-") else "Q"
        variant = (
            row["id"].split("zero-", 1)[1]
            if expected_family == "Z"
            else next(v for v in VARIANTS if row["id"].endswith("-" + v))
        )
        nominal = row["id"][: -len(variant)] + "nominal"
        if (row["family"], row["variant"], row["nominal"]) != (expected_family, variant, nominal):
            raise ValueError("manifest role metadata differs")
        for role in ("A", "B"):
            h = row[role]
            if not isinstance(h, str) or not re.fullmatch("[0-9a-f]{64}", h):
                raise ValueError("invalid input hash")
            if h not in seen:
                raw = read_bounded(out / "inputs" / f"{h}.raw", LENGTH * 2)
                if len(raw) != LENGTH * 2 or digest(raw) != h:
                    raise ValueError("input hash or length differs")
                seen.add(h)
                total += len(raw)
    if total != manifest["input_bytes"] or total > MAX_INPUT_BYTES:
        raise ValueError("input byte limit or total differs")
    if {p.name for p in (out / "inputs").iterdir()} != {h + ".raw" for h in seen}:
        raise ValueError("unexpected prepared input file")
    return manifest


def select_band(energy):
    if len(energy) != 21 or not all(math.isfinite(float(x)) for x in energy):
        raise ValueError("band energies must be finite and have 21 entries")
    return max(range(1, 21), key=lambda b: (energy[b], -b))


def boundary_search(evaluate):
    attempts = []

    def probe(a):
        attempts.append(a)
        value = float(evaluate(a))
        if not math.isfinite(value):
            raise ValueError("boundary reading must be finite")
        return value

    lo, hi = BOUNDARY["low"], BOUNDARY["high"]
    left, right = probe(lo), probe(hi)
    if not (left > 0 and right < 0):
        return {"status": "not_bracketed", "attempts": attempts, "lo": lo, "hi": hi}
    status = "iteration_limit"
    for _ in range(BOUNDARY["max_midpoints"]):
        if hi - lo <= 1:
            status = "adjacent"
            break
        mid = (lo + hi) // 2
        if mid in attempts:
            status = "repeated_midpoint"
            break
        value = probe(mid)
        if value == 0:
            lo = hi = mid
            status = "exact"
            break
        if value > 0:
            lo = mid
        else:
            hi = mid
    if status == "iteration_limit" and hi - lo <= 1:
        status = "adjacent"
    return {"status": status, "attempts": attempts, "lo": lo, "hi": hi}


# Research-only comparison and bounded persistence. Production code is imported, never modified.
def load_helper(path):
    import runpy
    from types import SimpleNamespace

    return SimpleNamespace(**runpy.run_path(str(path)))


class Budget:
    def __init__(self, out, *, clock=None, cancelled=None, max_bytes=None):
        import time

        self.out = Path(out)
        self.clock = clock or time.monotonic
        self.cancelled = cancelled or (lambda: False)
        self.started = self.clock()
        self.max_bytes = MAX_OUTPUT_BYTES if max_bytes is None else max_bytes
        self.bytes = 0
        self.files = {}

    def check(self):
        if self.cancelled():
            raise ValueError("research check cancelled")
        if self.clock() - self.started > MAX_SECONDS:
            raise ValueError("research time limit exceeded")

    def write_raw(self, name, data):
        self.check()
        if self.bytes + len(data) > self.max_bytes:
            raise ValueError("output byte limit exceeded")
        path = self.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_exclusive(path, data)
        self.bytes += len(data)
        self.files[name] = digest(data)
        self.check()
        return {"path": name, "sha256": digest(data)}

    def write(self, name, value):
        return self.write_raw(name, canonical(plain(value)))

    def append(self, name, value):
        self.check()
        data = canonical(value) + b"\n"
        if self.bytes + len(data) > self.max_bytes:
            raise ValueError("output byte limit exceeded")
        path = self.out / name
        with path.open("ab") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        self.bytes += len(data)
        self.files[name] = digest(path.read_bytes())
        self.check()


def plain(value):
    """Known numerical evidence represents undefined logarithms as null, with reasons/masks alongside it."""
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def numerical_grid(raw):
    from autonomous_mixing.adapters.dsp_analysis.front_end import dist10_clip_to_bands

    if len(raw) % 2 or len(raw) > LENGTH * 2:
        raise ValueError("input length exceeds recipe")
    counts = np.frombuffer(raw, dtype="<i2")
    r = dist10_clip_to_bands(counts, sample_rate_hz=RATE)
    return {
        k: getattr(r, k) for k in ("en", "thm", "call", "eligible", "nonzero_samples", "window_start", "window_end")
    }


def noise_and_quiet():
    from autonomous_mixing.adapters.dsp_analysis.directional_analysis import quiet_floor_bands

    helper = load_helper(REPO / "scripts/fl_studio/analyze_trial.py")
    return helper.rounding_noise_band_energy(), quiet_floor_bands()


def cell_indices(a, b):
    if not np.array_equal(a["call"], b["call"]) or not np.array_equal(a["eligible"], b["eligible"]):
        raise ValueError("source call geometry differs")
    return [(int(k), band) for k in np.flatnonzero(a["eligible"]) for band in range(1, 21)]


def margin(a, b):
    from autonomous_mixing.adapters.dsp_analysis.masking import directional_masking_margin_db

    return directional_masking_margin_db(a["thm"], b["en"]).margin_db


def structural_cells(a, b, quiet):
    m = margin(a, b)
    return [
        (k, band)
        for k, band in cell_indices(a, b)
        if a["nonzero_samples"][k] != 0
        and b["nonzero_samples"][k] != 0
        and np.isfinite(m[k, band])
        and a["thm"][k, band] > quiet[band]
    ]


def direction_result(a, b, noise, quiet, *, fixed_cells=None):
    cells = cell_indices(a, b)
    m = margin(a, b)
    with np.errstate(divide="ignore", invalid="ignore"):
        a_level = 10 * (np.log10(a["en"]) - np.log10(noise))
        b_level = 10 * (np.log10(b["en"]) - np.log10(noise))
    return {
        "cells": cells,
        "margin_db": m,
        "masker_energy": a["en"],
        "target_energy": b["en"],
        "masker_threshold": a["thm"],
        "masker_energy_above_noise_db": a_level,
        "target_energy_above_noise_db": b_level,
        "flags": {
            "masker_silent": [bool(a["nonzero_samples"][k] == 0) for k, _ in cells],
            "target_silent": [bool(b["nonzero_samples"][k] == 0) for k, _ in cells],
            "quiet_floor": [bool(a["thm"][k, band] == quiet[band]) for k, band in cells],
            "nonfinite_margin": [bool(not np.isfinite(m[k, band])) for k, band in cells],
        },
        "diagnostic_cells_C0": cells if fixed_cells is None else fixed_cells,
        "qualified_support": None,
        "undefined_reason": "null represents an undefined logarithm; exact-zero/nonfinite flags retained",
    }


def residual(got, ref):
    if not math.isfinite(float(got)) or not math.isfinite(float(ref)):
        raise ValueError("nonfinite residual input")
    return {
        "reference": float(ref),
        "observed": float(got),
        "absolute": abs(float(got) - float(ref)),
        "relative": abs(float(got) - float(ref)) / abs(float(ref)) if ref != 0 else None,
        "log_db": 10 * (math.log10(float(got)) - math.log10(float(ref))) if got > 0 and ref > 0 else None,
        "undefined_reason": None if got > 0 and ref > 0 else "nonpositive reference or observation",
    }


def reference_comparison(raw, records, expected_sha256):
    if digest(raw) != expected_sha256:
        raise ValueError("reference input hash mismatch")
    grid = numerical_grid(raw)
    if len(records) != len(grid["call"]):
        raise ValueError("reference metadata call count differs")
    n = len(raw) // 2
    cells = []
    for k, r in enumerate(records):
        expected = (k, n, int(grid["window_start"][k]), int(grid["window_end"][k]), int(grid["nonzero_samples"][k]))
        actual = tuple(
            r[field] for field in ("call", "clip_samples", "window_start", "window_end", "window_nonzero_samples")
        )
        eligible = k >= 2 and 576 * k + 256 <= n
        if (
            actual != expected
            or bool(grid["eligible"][k]) != eligible
            or r["window_end_within_clip"] != bool(grid["window_end"][k] <= n)
        ):
            raise ValueError("reference metadata differs")
        for band in range(21):
            cells.append(
                {
                    "call": k,
                    "band": band,
                    "eligible_supported": eligible and band != 0,
                    "startup_or_ineligible": not eligible,
                    "encoder_long_path": bool(r["encoder_long_path"]),
                    "energy": residual(grid["en"][k, band], r["en"][band]),
                    "threshold": residual(grid["thm"][k, band], r["thm"][band]),
                }
            )
    return {
        "category": "reference-reproduction",
        "source_sha256": expected_sha256,
        "metadata_equal": True,
        "cells": cells,
    }


def margin_residual(got, ref):
    if not math.isfinite(float(got)) or not math.isfinite(float(ref)):
        raise ValueError("nonfinite margin residual")
    return {
        "reference_db": float(ref),
        "observed_db": float(got),
        "delta_db": float(got - ref),
        "absolute_db": abs(float(got - ref)),
        "sign_changed": bool(np.sign(got) != np.sign(ref)),
    }


def case_category(row, *, claimed=None):
    category = {"Q": "internal-sensitivity", "Z": "internal-sensitivity", "B": "boundary-search"}[row["family"]]
    if claimed is not None and claimed != category:
        raise ValueError("new case cannot claim another evidence category")
    return category


def reference_evidence(cache, budget):
    import subprocess

    commands = []
    for name in ("verify_fixture.py", "compare_band_margins.py"):
        budget.check()
        command = [sys.executable, str(FIXTURE / "capture" / name)]
        remaining = max(0.001, MAX_SECONDS - (budget.clock() - budget.started))
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=remaining,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        record = {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout.decode("utf-8", errors="replace"),
        }
        commands.append(budget.write("reference/" + name + ".json", record))
        if result.returncode:
            raise ValueError("existing reference command failed")
    comparison = load_helper(FIXTURE / "capture/compare_band_margins.py")
    input_manifest = json.loads((FIXTURE / "inputs/inputs.json").read_text(encoding="utf-8"))
    refs, actual, source_rows = {}, {}, []
    for name in sorted(input_manifest):
        budget.check()
        raw = read_bounded(FIXTURE / "inputs" / (name + ".raw"), LENGTH * 2)
        expected = input_manifest[name]["sha256_int16le"]
        records = comparison.vf.records("expected_long_mapping", name)
        row = reference_comparison(raw, records, expected)
        source_rows.append(budget.write("reference/source-" + name + ".json", row))
        actual[name] = cache(expected, raw)
        refs[name] = {
            "en": np.array([r["en"] for r in records]),
            "thm": np.array([r["thm"] for r in records]),
            "eligible": actual[name]["eligible"],
            "call": actual[name]["call"],
        }
    paired = []
    pairs = [(a, b) for a, b in comparison.PAIRS for a, b in ((a, b), (b, a))] + list(comparison.CONTROLS)
    for i, (a, b) in enumerate(pairs):
        got, ref = margin(actual[a], actual[b]), margin(refs[a], refs[b])
        cells = [
            {
                "call": k,
                "band": band,
                "eligible_supported": bool(actual[a]["eligible"][k] and band != 0),
                "margin": margin_residual(got[k, band], ref[k, band]),
            }
            for k in range(len(got))
            for band in range(21)
        ]
        paired.append(
            budget.write(
                f"reference/pair-{i}.json",
                {"category": "reference-reproduction", "masker": a, "target": b, "cells": cells},
            )
        )
    return {"commands": commands, "sources": source_rows, "directions": paired}


def store_direction(row, direction, a_hash, b_hash, a, b, baseline, fixed, budget, noise, quiet):
    data = direction_result(a, b, noise, quiet, fixed_cells=fixed)
    base = direction_result(*baseline, noise, quiet, fixed_cells=fixed)
    delta = {}
    for key in ("masker_energy", "target_energy", "masker_threshold", "margin_db"):
        with np.errstate(invalid="ignore"):
            delta[key] = data[key] - base[key]
    data["delta_from_nominal"] = delta
    data["sign_changed"] = (
        (np.sign(data["margin_db"]) != np.sign(base["margin_db"]))
        & np.isfinite(data["margin_db"])
        & np.isfinite(base["margin_db"])
    )
    # Source grids persist once by count hash; pair records reference their full energies/thresholds/metadata.
    for key in ("masker_energy", "target_energy", "masker_threshold"):
        data.pop(key)
    data.update(
        category=case_category(row),
        id=row["id"],
        direction=direction,
        masker_sha256=a_hash,
        target_sha256=b_hash,
        nominal_id=row["nominal"],
    )
    return budget.write(f"cases/{row['id']}-{direction}.json", data)


def sensitivity_evidence(manifest, load, cache, budget, noise, quiet):
    lookup = {r["id"]: r for r in manifest["pairs"]}
    rows = []
    for row in manifest["pairs"]:
        budget.check()
        nominal = lookup[row["nominal"]]
        budget.append(
            "attempts.jsonl",
            {
                "id": row["id"],
                "status": "pending",
                "category": case_category(row),
                "A": row["A"],
                "B": row["B"],
                "directions": ["A-to-B", "B-to-A"],
            },
        )
        a, b = cache(row["A"], load(row["A"])), cache(row["B"], load(row["B"]))
        na, nb = cache(nominal["A"], load(nominal["A"])), cache(nominal["B"], load(nominal["B"]))
        for direction, ah, bh, ag, bg, nominal_grids in (
            ("A-to-B", row["A"], row["B"], a, b, (na, nb)),
            ("B-to-A", row["B"], row["A"], b, a, (nb, na)),
        ):
            fixed = structural_cells(*nominal_grids, quiet)
            rows.append(store_direction(row, direction, ah, bh, ag, bg, nominal_grids, fixed, budget, noise, quiet))
        budget.append("attempts.jsonl", {"id": row["id"], "status": "stored"})
    return rows


def boundary_evidence(cache, budget, noise, quiet, input_bytes):
    a_raw = quantize(tone(8192, 1000))
    a_hash = digest(a_raw)
    a = cache(a_hash, a_raw)
    band = select_band(a["en"][BOUNDARY["call"]])
    rows, seen, total = [], set(), input_bytes
    for phase_index, phi in enumerate(BOUNDARY["phases"]):
        observations = []

        def evaluate(amplitude):
            nonlocal total
            raw = quantize(tone(amplitude, 1000, phi))
            h = digest(raw)
            # Capture exact search-generated inputs and pending intent before any use in DSP.
            if h not in seen:
                if total + len(raw) > MAX_INPUT_BYTES:
                    raise ValueError("boundary input byte limit exceeded")
                budget.write_raw(f"boundary-inputs/{h}.raw", raw)
                total += len(raw)
                seen.add(h)
            row = {"id": f"boundary-{phase_index}-{amplitude}", "family": "B", "nominal": "not-applicable"}
            budget.append(
                "attempts.jsonl",
                {
                    "id": row["id"],
                    "status": "pending",
                    "A": a_hash,
                    "B": h,
                    "category": "boundary-search",
                    "directions": ["A-to-B", "B-to-A"],
                },
            )
            b = cache(h, raw)
            observed = float(margin(a, b)[BOUNDARY["call"], band])
            observations.append({"amplitude": amplitude, "sha256": h, "margin_db": observed})
            for direction, ah, bh, ag, bg in (("A-to-B", a_hash, h, a, b), ("B-to-A", h, a_hash, b, a)):
                rows.append(store_direction(row, direction, ah, bh, ag, bg, (ag, bg), [], budget, noise, quiet))
            budget.append("attempts.jsonl", {"id": row["id"], "status": "stored", "selected_margin_db": observed})
            return observed

        result = boundary_search(evaluate)
        result.update(
            phase=phi,
            selected_band=band,
            selected_call=BOUNDARY["call"],
            band_selection_energy=a["en"][BOUNDARY["call"]],
            readings=observations,
            nearest_tested_absolute_margin_db=min(abs(r["margin_db"]) for r in observations),
            category="boundary-search",
        )
        budget.write(f"boundary-phase-{phase_index}.json", result)
    return rows


def run(out, *, cancelled=None):
    out = Path(out)
    manifest = load_prepared(out)  # Every frozen input is verified before any DSP/output run begins.
    if not (out / "run-1").exists():
        number = 1
    elif not (out / "run-2").exists():
        if not (out / "run-1/completed.json").is_file():
            raise ValueError("previous run incomplete; automatic retry refused")
        number = 2
    else:
        raise ValueError("only two runs registered")
    target = out / f"run-{number}"
    target.mkdir(exist_ok=False)
    budget = Budget(target, cancelled=cancelled)
    try:
        noise, quiet = noise_and_quiet()
        noise_helper = load_helper(REPO / "scripts/fl_studio/analyze_trial.py")
        budget.write(
            "diagnostic-noise.json",
            {
                "per_bin_energy": noise_helper.rounding_noise_per_bin(),
                "partition_energy": noise_helper.rounding_noise_partition_energy(),
                "band_energy": noise,
                "quiet_floor": quiet,
                "helper_sha256": digest((REPO / "scripts/fl_studio/analyze_trial.py").read_bytes()),
                "noise_model": "uniform variance 1/12; diagnostic, not measured or qualified",
            },
        )
        cache_values = {}

        def cache(h, raw):
            budget.check()
            if digest(raw) != h:
                raise ValueError("source hash differs before DSP")
            if h not in cache_values:
                grid = numerical_grid(raw)
                budget.write(f"grids/{h}.json", grid)
                cache_values[h] = grid
            return cache_values[h]

        def load(h):
            raw = read_bounded(out / "inputs" / f"{h}.raw", LENGTH * 2)
            if digest(raw) != h:
                raise ValueError("prepared input changed during run")
            return raw

        reference = reference_evidence(cache, budget)
        sensitivity = sensitivity_evidence(manifest, load, cache, budget, noise, quiet)
        boundary = boundary_evidence(cache, budget, noise, quiet, manifest["input_bytes"])
        for n in (1407, 1408):
            grid = numerical_grid(quantize(tone(8192, 1000))[: n * 2])
            expected = [] if n == 1407 else [2]
            if np.flatnonzero(grid["eligible"]).tolist() != expected:
                raise ValueError("complete-window prefix boundary differs")
            budget.write(f"geometry-{n}.json", grid)
        if load_prepared(out) != manifest:
            raise ValueError("prepared data changed during run")
        files = dict(budget.files)
        reproduced = False
        if number == 2:
            previous = json.loads(read_bounded(out / "run-1/completed.json", 1 << 20))
            if previous["numerical_files"] != files:
                raise ValueError("repeat differs from first run")
            for name, h in previous["numerical_files"].items():
                if digest(read_bounded(out / "run-1" / name, MAX_OUTPUT_BYTES)) != h:
                    raise ValueError("first-run evidence changed")
            reproduced = True
        result = {
            "completed": True,
            "recipe": RECIPE["version"],
            "qualified_support": None,
            "policy_values_selected": False,
            "reference": reference,
            "sensitivity": sensitivity,
            "boundary": boundary,
            "numerical_files": files,
            "reproduces_run_1": reproduced,
            "elapsed_seconds": budget.clock() - budget.started,
            "manifest_sha256": digest((out / "control-inputs.json").read_bytes()),
        }
        payload = canonical(plain(result))
        budget.check()
        if budget.bytes + len(payload) > budget.max_bytes:
            raise ValueError("output byte limit exceeded")
        pending = target / ".completion.pending"
        write_exclusive(pending, payload)
        budget.check()  # Never publish a success marker after cancellation/timeout during its write.
        pending.rename(target / "completed.json")
        return result
    except BaseException as error:
        failure = {
            "completed": False,
            "exception": type(error).__name__,
            "message": str(error)[:1000],
            "qualified_support": None,
            "stored_files": len(budget.files),
        }
        write_exclusive(target / "failure.json", canonical(failure))
        raise


def main(argv=None):
    import signal
    import threading

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "run"))
    parser.add_argument("--out", type=Path, default=Path("C:/uni/Bachelor-Thesis/test/p1-small-numerical-controls"))
    args = parser.parse_args(argv)
    cancel = threading.Event()
    previous = signal.signal(signal.SIGINT, lambda *_: cancel.set())
    try:
        if args.phase == "prepare":
            prepare(args.out, cancelled=cancel.is_set)
        else:
            run(args.out, cancelled=cancel.is_set)
        print(args.phase + " complete; numerical engineering evidence only, no qualification activated")
        return 0
    except (ValueError, OSError) as error:
        print(f"refused/incomplete: {error}", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    raise SystemExit(main())
