"""Bounded research checker tests: temporary generated numerical controls, never music or evaluation."""

import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/research/check_p1_numerical_controls.py"
spec = importlib.util.spec_from_file_location("p1_controls", SCRIPT)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def test_half_even_and_int16_boundaries():
    raw = c.quantize([0, 0.5, 1.5, 2.5, -0.5, -1.5, -32768, 32767])
    assert np.frombuffer(raw, dtype="<i2").tolist() == [0, 0, 2, 2, 0, -2, -32768, 32767]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -32769, 32768])
def test_invalid_samples_refused_without_clipping(value):
    with pytest.raises(ValueError):
        c.quantize([value])


def test_fixed_recipe_counts_and_independent_perturbations():
    pairs = c.control_pairs()
    q = [p for p in pairs if p["family"] == "Q"]
    z = [p for p in pairs if p["family"] == "Z"]
    assert len(q) == 60 and len(z) == 5 and len({p["id"] for p in pairs}) == 65
    lookup = {p["id"]: p for p in pairs}
    for row in q:
        nominal = lookup[row["nominal"]]
        assert len(row["A"]) == len(row["B"]) == 13824
        for role in ("A", "B"):
            a = np.frombuffer(row[role], dtype="<i2").astype(int)
            b = np.frombuffer(nominal[role], dtype="<i2").astype(int)
            assert np.max(np.abs(a - b)) <= 1
            if not row["variant"].startswith(role):
                assert row[role] == nominal[role]
            else:
                assert row[role] != nominal[role]
    assert set(np.frombuffer(z[1]["A"], dtype="<i2")) == {0}
    assert set(np.frombuffer(z[2]["B"], dtype="<i2")) == {0}
    assert np.all(np.frombuffer(z[3]["A"], dtype="<i2")[3456:] == 0)


def test_offsets_are_before_rounding():
    assert np.frombuffer(c.quantize([0.49, 0.51], 0.5), dtype="<i2").tolist() == [1, 1]
    assert np.frombuffer(c.quantize([0.49, 0.51]), dtype="<i2").tolist() == [0, 1]


def test_preparation_round_trip_and_refuses_overwrite(tmp_path):
    out = tmp_path / "controls"
    manifest = c.prepare(out)
    assert c.load_prepared(out) == manifest
    assert len(manifest["pairs"]) == 65
    assert manifest["boundary"]["max_midpoints"] == 15
    with pytest.raises(FileExistsError):
        c.prepare(out)


@pytest.mark.parametrize("tamper", ["manifest", "input", "missing", "source"])
def test_prepared_tampering_refused(tmp_path, monkeypatch, tamper):
    out = tmp_path / "controls"
    manifest = c.prepare(out)
    if tamper == "manifest":
        (out / "control-inputs.json").write_bytes(b"{}")
    elif tamper in ("input", "missing"):
        p = out / "inputs" / (manifest["pairs"][0]["A"] + ".raw")
        if tamper == "input":
            p.write_bytes(bytes(p.stat().st_size))
        else:
            p.unlink()
    else:
        monkeypatch.setattr(c, "fingerprint", lambda: {"changed": True})
    with pytest.raises((ValueError, OSError)):
        c.load_prepared(out)


def test_input_size_limit_is_enforced_before_persisting_more(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "MAX_INPUT_BYTES", 1)
    with pytest.raises(ValueError, match="input.*limit"):
        c.prepare(tmp_path / "controls")


def test_boundary_search_bracket_early_stop_and_cap():
    attempts = []

    def evaluate(a):
        attempts.append(a)
        return 100.5 - a

    result = c.boundary_search(evaluate)
    assert result["status"] == "adjacent" and len(attempts) <= 17
    assert result["lo"] == 100 and result["hi"] == 101
    assert result["attempts"] == attempts
    assert c.boundary_search(lambda a: 1)["status"] == "not_bracketed"
    assert c.boundary_search(lambda a: 16384 - a)["status"] == "exact"
    assert c.select_band([0, 10, 10] + [0] * 18) == 1


def test_nonfinite_boundary_reading_is_refused():
    with pytest.raises(ValueError, match="finite"):
        c.boundary_search(lambda a: math.nan)


def test_complete_grid_and_prefix_boundaries_keep_startup():
    raw = c.quantize(c.tone(8192, 1000))
    grid = c.numerical_grid(raw)
    assert grid["call"].tolist() == list(range(12))
    assert np.flatnonzero(grid["eligible"]).tolist() == list(range(2, 12))
    assert not c.numerical_grid(raw[: 1407 * 2])["eligible"].any()
    assert np.flatnonzero(c.numerical_grid(raw[: 1408 * 2])["eligible"]).tolist() == [2]
    assert grid["en"].shape == grid["thm"].shape == (12, 21)


def test_direction_reverses_values_and_silence_uses_original_counts():
    a = c.numerical_grid(bytes(13824))
    b = c.numerical_grid(c.quantize(c.tone(1024, 2000)))
    noise, quiet = c.noise_and_quiet()
    ab = c.direction_result(a, b, noise, quiet)
    ba = c.direction_result(b, a, noise, quiet)
    assert all(ab["flags"]["masker_silent"]) and not any(ab["flags"]["target_silent"])
    assert all(ba["flags"]["target_silent"]) and not any(ba["flags"]["masker_silent"])
    assert np.all(a["en"] > 0) and all(ab["flags"]["quiet_floor"])
    np.testing.assert_array_equal(ab["target_energy"], b["en"])
    np.testing.assert_array_equal(ba["target_energy"], a["en"])
    assert len(ab["cells"]) == 200 and len(ab["margin_db"]) == 12


def test_c0_is_preserved_after_target_muting(tmp_path):
    a = c.numerical_grid(c.quantize(c.tone(8192, 1000)))
    b = c.numerical_grid(c.quantize(c.tone(1024, 2000)))
    muted = c.numerical_grid(bytes(13824))
    noise, quiet = c.noise_and_quiet()
    fixed = c.structural_cells(a, b, quiet)
    assert len(fixed) > 0
    result = c.direction_result(a, muted, noise, quiet, fixed_cells=fixed)
    assert result["diagnostic_cells_C0"] == fixed
    assert result["qualified_support"] is None
    assert all(result["flags"]["target_silent"])
    budget = c.Budget(tmp_path)
    row = {"family": "Z", "id": "labelled-muted", "nominal": "labelled-nominal"}
    saved = c.store_direction(row, "A-to-B", "labelled-a", "labelled-b", a, muted, (a, b), fixed, budget, noise, quiet)
    evidence = json.loads((tmp_path / saved["path"]).read_text())
    assert evidence["diagnostic_cells_C0"] == [list(cell) for cell in fixed]
    assert all(evidence["flags"]["target_silent"])


def test_noise_reference_includes_nyquist():
    noise, _ = c.noise_and_quiet()
    helper = c.load_helper(c.REPO / "scripts/fl_studio/analyze_trial.py")
    partitions = helper.rounding_noise_partition_energy()
    assert partitions[0] == pytest.approx(49 * helper.rounding_noise_per_bin())
    np.testing.assert_array_equal(noise, helper.rounding_noise_band_energy())


def test_reference_residuals_are_per_cell_and_check_input_binding():
    name = "masker_1k_a8192"
    raw = (c.FIXTURE / "inputs" / f"{name}.raw").read_bytes()
    records = [json.loads(s) for s in (c.FIXTURE / "expected_long_mapping" / f"{name}.jsonl").read_text().splitlines()]
    out = c.reference_comparison(raw, records, c.digest(raw))
    assert out["category"] == "reference-reproduction" and len(out["cells"]) == 12 * 21
    assert out["cells"][0]["call"] == 0 and out["cells"][0]["band"] == 0
    assert sum(x["eligible_supported"] for x in out["cells"]) == 200
    assert out["metadata_equal"]
    with pytest.raises(ValueError, match="hash"):
        c.reference_comparison(bytes(len(raw)), records, c.digest(raw))
    altered = [dict(r) for r in records]
    altered[2]["window_nonzero_samples"] = 0
    with pytest.raises(ValueError, match="metadata"):
        c.reference_comparison(raw, altered, c.digest(raw))


def test_zero_reference_has_no_fabricated_relative_or_log_error():
    residual = c.residual(2.0, 0.0)
    assert residual["absolute"] == 2 and residual["relative"] is None and residual["log_db"] is None
    assert residual["undefined_reason"] == "nonpositive reference or observation"


def test_canonical_json_refuses_nonfinite_numbers():
    with pytest.raises(ValueError):
        c.canonical({"value": math.inf})


def test_budget_enforces_cancellation_time_and_bytes(tmp_path):
    with pytest.raises(ValueError, match="cancel"):
        c.Budget(tmp_path, cancelled=lambda: True).check()
    times = iter((0, 301))
    with pytest.raises(ValueError, match="time"):
        c.Budget(tmp_path, clock=lambda: next(times)).check()
    budget = c.Budget(tmp_path, max_bytes=4)
    with pytest.raises(ValueError, match="output.*limit"):
        budget.write("large.json", {"text": "too long"})
    assert not (tmp_path / "large.json").exists()


def test_failed_run_records_failure_and_never_completes(tmp_path, monkeypatch):
    out = tmp_path / "controls"
    c.prepare(out)
    monkeypatch.setattr(c, "reference_evidence", lambda *a, **k: (_ for _ in ()).throw(ValueError("labelled failure")))
    with pytest.raises(ValueError, match="labelled failure"):
        c.run(out)
    failure = json.loads((out / "run-1/failure.json").read_text())
    assert failure["completed"] is False and not (out / "run-1/completed.json").exists()


def test_new_case_cannot_claim_independent_reference():
    with pytest.raises(ValueError, match="category"):
        c.case_category({"family": "Q"}, claimed="reference-reproduction")
    assert c.case_category({"family": "Q"}) == "internal-sensitivity"


def test_run_checks_all_inputs_before_any_dsp(tmp_path, monkeypatch):
    out = tmp_path / "controls"
    m = c.prepare(out)
    (out / "inputs" / (m["pairs"][-1]["B"] + ".raw")).unlink()
    monkeypatch.setattr(c, "numerical_grid", lambda *a: pytest.fail("DSP before input verification"))
    with pytest.raises((OSError, ValueError)):
        c.run(out)


def test_real_comparison_path_uses_different_source_grids(tmp_path, monkeypatch):
    out = tmp_path / "controls"
    c.prepare(out)
    # Labelled test-only reduced execution: production recipe/CLI has no subset switch.
    monkeypatch.setattr(c, "reference_evidence", lambda *a: {"labelled_test_only": True})
    monkeypatch.setattr(c, "boundary_evidence", lambda *a: {"labelled_test_only": True})
    real = c.sensitivity_evidence
    seen = []

    def reduced(manifest, load, cache, budget, noise, quiet):
        subset = dict(manifest, pairs=manifest["pairs"][:2])
        result = real(subset, load, cache, budget, noise, quiet)
        seen.extend(result)
        return result

    monkeypatch.setattr(c, "sensitivity_evidence", reduced)
    result = c.run(out)
    assert result["completed"] and len(seen) == 4
    case = json.loads((out / "run-1" / seen[0]["path"]).read_text())
    other = json.loads((out / "run-1" / seen[1]["path"]).read_text())
    assert case["masker_sha256"] == other["target_sha256"]
    assert case["target_sha256"] == other["masker_sha256"]
    assert case["masker_sha256"] != case["target_sha256"]
    assert case["category"] == "internal-sensitivity"
    a_raw = (out / "inputs" / (case["masker_sha256"] + ".raw")).read_bytes()
    b_raw = (out / "inputs" / (case["target_sha256"] + ".raw")).read_bytes()
    expected_reverse = c.margin(c.numerical_grid(b_raw), c.numerical_grid(a_raw))
    np.testing.assert_array_equal(other["margin_db"], expected_reverse)

    repeated = c.run(out)
    assert repeated["reproduces_run_1"]
    with pytest.raises(ValueError, match="two runs"):
        c.run(out)


def reduced_run_setup(tmp_path, monkeypatch):
    out = tmp_path / "controls"
    c.prepare(out)
    monkeypatch.setattr(c, "reference_evidence", lambda *a: {"labelled_test_only": True})
    monkeypatch.setattr(c, "boundary_evidence", lambda *a: {"labelled_test_only": True})
    real = c.sensitivity_evidence
    monkeypatch.setattr(
        c, "sensitivity_evidence", lambda manifest, *args: real(dict(manifest, pairs=manifest["pairs"][:1]), *args)
    )
    return out


def test_completion_is_not_published_if_cancelled_during_final_write(tmp_path, monkeypatch):
    out = reduced_run_setup(tmp_path, monkeypatch)
    cancelled = [False]
    write = c.write_exclusive

    def cancel_at_marker(path, data):
        write(path, data)
        if path.name in ("completed.json", ".completion.pending"):
            cancelled[0] = True

    monkeypatch.setattr(c, "write_exclusive", cancel_at_marker)
    with pytest.raises(ValueError, match="cancel"):
        c.run(out, cancelled=lambda: cancelled[0])
    assert not (out / "run-1/completed.json").exists()
    assert (out / "run-1/failure.json").exists()


def test_preparation_honours_cancel_before_generating_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "control_pairs", lambda: pytest.fail("generated after cancellation"))
    with pytest.raises(ValueError, match="cancel"):
        c.prepare(tmp_path / "controls", cancelled=lambda: True)


def test_reference_window_end_flag_is_checked():
    name = "masker_1k_a8192"
    raw = (c.FIXTURE / "inputs" / f"{name}.raw").read_bytes()
    records = [json.loads(s) for s in (c.FIXTURE / "expected_long_mapping" / f"{name}.jsonl").read_text().splitlines()]
    records[2]["window_end_within_clip"] = False
    with pytest.raises(ValueError, match="metadata"):
        c.reference_comparison(raw, records, c.digest(raw))


def test_existing_reference_path_is_exercised_in_temp_evidence(tmp_path):
    budget = c.Budget(tmp_path)
    grids = {}

    def cache(h, raw):
        grids.setdefault(h, c.numerical_grid(raw))
        return grids[h]

    result = c.reference_evidence(cache, budget)
    assert len(result["commands"]) == 2 and len(result["sources"]) == 9
    assert len(result["directions"]) == 8
    assert all(
        json.loads((tmp_path / r["path"]).read_text())["category"] == "reference-reproduction"
        for r in result["sources"]
    )


def test_boundary_real_dsp_records_inputs_before_calculation(tmp_path, monkeypatch):
    budget = c.Budget(tmp_path)
    seen = []

    def search(evaluate):
        # Labelled unit test: only endpoint attempts, not a registered boundary run.
        evaluate(1)
        evaluate(32767)
        return {"status": "labelled_test_only", "attempts": [1, 32767]}

    monkeypatch.setattr(c, "boundary_search", search)

    def cache(h, raw):
        if h != c.digest(c.quantize(c.tone(8192, 1000))):
            assert (tmp_path / "boundary-inputs" / f"{h}.raw").is_file()
            assert h in (tmp_path / "attempts.jsonl").read_text()
        seen.append(h)
        return c.numerical_grid(raw)

    noise, quiet = c.noise_and_quiet()
    rows = c.boundary_evidence(cache, budget, noise, quiet, 0)
    assert len(rows) == 8 and len(seen) == 5
    assert (tmp_path / "boundary-phase-0.json").is_file()


def test_margin_residual_is_in_db_not_a_logarithm_of_db():
    result = c.margin_residual(2.0, -1.0)
    assert result == {
        "reference_db": -1.0,
        "observed_db": 2.0,
        "delta_db": 3.0,
        "absolute_db": 3.0,
        "sign_changed": True,
    }
    assert "log_db" not in result and "relative" not in result


def test_noise_evidence_records_partition_reference(tmp_path, monkeypatch):
    out = reduced_run_setup(tmp_path, monkeypatch)
    c.run(out)
    data = json.loads((out / "run-1/diagnostic-noise.json").read_text())
    assert data["partition_energy"][0] == pytest.approx(49 * data["per_bin_energy"])
    assert len(data["partition_energy"]) == 63 and len(data["band_energy"]) == 21
