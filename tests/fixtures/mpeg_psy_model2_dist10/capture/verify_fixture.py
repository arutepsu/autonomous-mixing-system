"""Check the dist10 Model 2 fixture without running the reference.

1. Inputs regenerate byte-identically, and every recorded SHA-256 (inputs, both expected sets, both probe patches,
   the probe B harness source) matches the files on disk.
2. Relations between the two captures, all exact (no tolerance):
   - probe B reports the same calls and pe as probe A, and its encoder branch flag equals A's long_path;
   - on every call where the encoder took the long path, probe B's en/thm equal probe A's bit for bit;
   - on every short-path call, probe B has 21 finite positive en/thm values;
   - final-frame case: the truncated clip's calls whose window ends inside the clip equal the full-length
     clip's calls; the call whose window reaches past the end differs;
   - silent-masker control: for digital silence thm == thm_quiet == thm_from_quiet_partitions on every call;
   - activity: across all probe B records, en equals the silence (FFT-floor) values exactly when the analysed
     window has no nonzero sample.

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


def main() -> int:
    provenance = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    check_hashes(provenance, failures)
    check_relations(failures)
    for failure in failures:
        print("FAIL:", failure)
    if not failures:
        print("fixture verified: inputs regenerate, all recorded hashes match, probe relations hold")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
