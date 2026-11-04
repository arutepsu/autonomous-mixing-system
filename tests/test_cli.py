import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from autonomous_mixing.drivers.cli import main

SR = 44100


def _stems(tmp_path, bass_sr=SR):
    t = np.arange(2 * SR) / SR
    kick = tmp_path / "kick.wav"
    bass = tmp_path / "bass.wav"
    sf.write(kick, 0.5 * np.sin(2 * np.pi * 60 * t) * np.exp(-8 * (t % 0.5)), SR)
    sf.write(bass, 0.2 * np.sin(2 * np.pi * 55 * np.arange(2 * bass_sr) / bass_sr), bass_sr)
    return kick, bass


def test_analyze_writes_json_report(tmp_path):
    kick, bass = _stems(tmp_path)
    out = tmp_path / "out" / "analysis.json"
    assert main(["analyze", str(kick), str(bass), "--output", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["schema_version"] == "m0.1"
    assert report["sample_rate_hz"] == SR
    assert report["duration_seconds"] == 2.0
    for stem in ("kick", "bass"):
        assert len(report[stem]["sha256"]) == 64
        assert report[stem]["peak_dbfs"] <= 0.0
        assert report[stem]["low_band_power_proxy"] > 0.0
    assert 0.0 <= report["temporal_coactivity_fraction"] <= 1.0


def test_analyze_is_deterministic(tmp_path):
    kick, bass = _stems(tmp_path)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    main(["analyze", str(kick), str(bass), "--output", str(a)])
    main(["analyze", str(kick), str(bass), "--output", str(b)])
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")


def test_incompatible_inputs_reported_without_traceback(tmp_path, capsys):
    kick, bass = _stems(tmp_path, bass_sr=48000)
    out = tmp_path / "analysis.json"
    assert main(["analyze", str(kick), str(bass), "--output", str(out)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: Stems must share a sample rate")
    assert "Traceback" not in err
    assert not out.exists()


def test_missing_input_reported(tmp_path, capsys):
    kick, _ = _stems(tmp_path)
    assert main(["analyze", str(kick), str(tmp_path / "nope.wav"), "--output", str(tmp_path / "x.json")]) == 2
    assert "not found" in capsys.readouterr().err


# --- Report destination must not alias an input (pre-write identity guard) ---

SUCCESS_PREFIX = "Wrote "


def _sha256(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_rejected(code, capsys, protected):
    captured = capsys.readouterr()
    assert code == 2
    assert captured.err.startswith("error:")
    assert "Traceback" not in captured.err
    assert SUCCESS_PREFIX not in captured.out
    for path, digest in protected.items():
        assert _sha256(path) == digest, path


@pytest.mark.parametrize("which", ["kick", "bass"])
def test_output_equal_to_input_is_rejected(tmp_path, capsys, which):
    kick, bass = _stems(tmp_path)
    protected = {kick: _sha256(kick), bass: _sha256(bass)}
    target = kick if which == "kick" else bass
    code = main(["analyze", str(kick), str(bass), "--output", str(target)])
    _assert_rejected(code, capsys, protected)


@pytest.mark.parametrize("create_intermediate", [True, False])
def test_relative_output_aliasing_absolute_input_is_rejected(tmp_path, capsys, monkeypatch, create_intermediate):
    # Without the intermediate directory, POSIX resolves "sub/.." only after the
    # CLI creates the report parent; Windows normalizes it lexically.
    kick, bass = _stems(tmp_path)
    protected = {kick: _sha256(kick), bass: _sha256(bass)}
    if create_intermediate:
        (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path)
    code = main(["analyze", str(kick.resolve()), str(bass.resolve()), "--output", "./sub/../bass.wav"])
    _assert_rejected(code, capsys, protected)


def test_absolute_output_aliasing_relative_input_is_rejected(tmp_path, capsys, monkeypatch):
    kick, bass = _stems(tmp_path)
    protected = {kick: _sha256(kick), bass: _sha256(bass)}
    monkeypatch.chdir(tmp_path)
    code = main(["analyze", "kick.wav", "bass.wav", "--output", str(kick.resolve())])
    _assert_rejected(code, capsys, protected)


@pytest.mark.parametrize("which", ["kick", "bass"])
def test_symlink_output_to_input_is_rejected(tmp_path, capsys, which):
    kick, bass = _stems(tmp_path)
    target = kick if which == "kick" else bass
    alias = tmp_path / "alias.json"
    try:
        os.symlink(target, alias)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable on this platform: {exc!r}")
    protected = {kick: _sha256(kick), bass: _sha256(bass), alias: _sha256(alias)}
    code = main(["analyze", str(kick), str(bass), "--output", str(alias)])
    _assert_rejected(code, capsys, protected)
    assert alias.is_symlink()


@pytest.mark.parametrize("which", ["kick", "bass"])
def test_hardlink_output_to_input_is_rejected(tmp_path, capsys, which):
    kick, bass = _stems(tmp_path)
    target = kick if which == "kick" else bass
    alias = tmp_path / "hardlink.json"
    try:
        os.link(target, alias)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hard link creation unavailable on this platform: {exc!r}")
    protected = {kick: _sha256(kick), bass: _sha256(bass), alias: _sha256(alias)}
    code = main(["analyze", str(kick), str(bass), "--output", str(alias)])
    _assert_rejected(code, capsys, protected)


def test_unrelated_new_report_without_json_extension_succeeds(tmp_path, capsys):
    kick, bass = _stems(tmp_path)
    protected = {kick: _sha256(kick), bass: _sha256(bass)}
    out = tmp_path / "new" / "nested" / "report.txt"
    assert main(["analyze", str(kick), str(bass), "--output", str(out)]) == 0
    assert SUCCESS_PREFIX in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == "m0.1"
    for path, digest in protected.items():
        assert _sha256(path) == digest


def test_unrelated_existing_report_is_replaced(tmp_path, capsys):
    kick, bass = _stems(tmp_path)
    protected = {kick: _sha256(kick), bass: _sha256(bass)}
    out = tmp_path / "analysis.json"
    out.write_text("stale", encoding="utf-8")
    assert main(["analyze", str(kick), str(bass), "--output", str(out)]) == 0
    assert SUCCESS_PREFIX in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == "m0.1"
    for path, digest in protected.items():
        assert _sha256(path) == digest


def test_injected_destination_stat_failure_prevents_writing(tmp_path, capsys, monkeypatch):
    # INJECTED failure: os.stat raises PermissionError only for the existing report
    # destination, simulating an inspection failure that real permissions could cause.
    kick, bass = _stems(tmp_path)
    out = tmp_path / "existing.json"
    out.write_text("previous report", encoding="utf-8")
    protected = {kick: _sha256(kick), bass: _sha256(bass), out: _sha256(out)}
    real_stat = os.stat

    def failing_stat(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(out):
            raise PermissionError(13, "injected permission denied", os.fspath(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", failing_stat)
    code = main(["analyze", str(kick), str(bass), "--output", str(out)])
    monkeypatch.undo()
    _assert_rejected(code, capsys, protected)
    assert out.read_text(encoding="utf-8") == "previous report"


def test_input_validation_precedes_report_alias_check(tmp_path, capsys):
    kick, bass = _stems(tmp_path, bass_sr=48000)
    protected = {kick: _sha256(kick), bass: _sha256(bass)}
    code = main(["analyze", str(kick), str(bass), "--output", str(kick)])
    err = capsys.readouterr().err
    assert code == 2
    assert err.startswith("error: Stems must share a sample rate")
    for path, digest in protected.items():
        assert _sha256(path) == digest


# --- Report parent directory cannot be created (expected filesystem failures) ---


def _assert_parent_rejected(code, capsys, protected, report):
    _assert_rejected(code, capsys, protected)
    assert not os.path.lexists(report)


def test_existing_file_as_report_parent_is_rejected(tmp_path, capsys):
    # Windows: stat reports the path missing, so mkdir raises FileExistsError.
    # POSIX: stat raises NotADirectoryError, so the identity inspection rejects first.
    kick, bass = _stems(tmp_path)
    blocker = tmp_path / "plain.txt"
    blocker.write_text("not a directory", encoding="utf-8")
    protected = {kick: _sha256(kick), bass: _sha256(bass), blocker: _sha256(blocker)}
    report = blocker / "report.json"
    code = main(["analyze", str(kick), str(bass), "--output", str(report)])
    _assert_parent_rejected(code, capsys, protected, report)


@pytest.mark.parametrize("which", ["kick", "bass"])
def test_input_wav_as_report_parent_is_rejected(tmp_path, capsys, which):
    kick, bass = _stems(tmp_path)
    protected = {kick: _sha256(kick), bass: _sha256(bass)}
    report = (kick if which == "kick" else bass) / "report.json"
    code = main(["analyze", str(kick), str(bass), "--output", str(report)])
    _assert_parent_rejected(code, capsys, protected, report)


def test_nested_report_parent_through_existing_file_is_rejected(tmp_path, capsys):
    kick, bass = _stems(tmp_path)
    blocker = tmp_path / "plain.txt"
    blocker.write_text("not a directory", encoding="utf-8")
    protected = {kick: _sha256(kick), bass: _sha256(bass), blocker: _sha256(blocker)}
    report = blocker / "sub" / "report.json"
    code = main(["analyze", str(kick), str(bass), "--output", str(report)])
    _assert_parent_rejected(code, capsys, protected, report)


def _inject_parent_mkdir_failure(monkeypatch, parent, exc):
    real_mkdir = Path.mkdir

    def failing_mkdir(self, *args, **kwargs):
        if os.fspath(self) == os.fspath(parent):
            raise exc
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)


@pytest.mark.parametrize("exc_type", [FileExistsError, NotADirectoryError, PermissionError, FileNotFoundError])
def test_injected_expected_parent_creation_failure_is_rejected(tmp_path, capsys, monkeypatch, exc_type):
    # INJECTED: Path.mkdir raises the expected error type for the report parent only.
    kick, bass = _stems(tmp_path)
    protected = {kick: _sha256(kick), bass: _sha256(bass)}
    report = tmp_path / "newdir" / "report.json"
    _inject_parent_mkdir_failure(monkeypatch, report.parent, exc_type("injected mkdir failure"))
    code = main(["analyze", str(kick), str(bass), "--output", str(report)])
    monkeypatch.undo()
    _assert_parent_rejected(code, capsys, protected, report)
    assert not report.parent.exists()


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("injected unexpected error"), OSError(errno.EIO, "injected I/O error")],
    ids=["RuntimeError", "OSError-EIO"],
)
def test_injected_unexpected_parent_creation_error_propagates(tmp_path, capsys, monkeypatch, exc):
    # INJECTED: errors outside the expected set must propagate unchanged.
    assert type(exc) in (RuntimeError, OSError)
    kick, bass = _stems(tmp_path)
    protected = {kick: _sha256(kick), bass: _sha256(bass)}
    report = tmp_path / "newdir" / "report.json"
    _inject_parent_mkdir_failure(monkeypatch, report.parent, exc)
    with pytest.raises(type(exc)) as excinfo:
        main(["analyze", str(kick), str(bass), "--output", str(report)])
    monkeypatch.undo()
    assert excinfo.value is exc
    assert SUCCESS_PREFIX not in capsys.readouterr().out
    assert not report.exists()
    for path, digest in protected.items():
        assert _sha256(path) == digest


# Module execution in a real subprocess (ADR-027 steps M3/M4): calling ``main`` directly would not catch a
# lost ``__main__`` guard, as happened to the M3 shim. The console script is checked in tests/architecture.
MODULE_PATHS = ["autonomous_mixing.drivers.cli"]


def _run_module(module, *args):
    return subprocess.run([sys.executable, "-m", module, *args], capture_output=True, text=True, check=False)


@pytest.mark.parametrize("module", MODULE_PATHS)
def test_module_execution_prints_help(module):
    result = _run_module(module, "--help")
    assert result.returncode == 0
    assert result.stdout.startswith("usage: autonomous-mixing")


@pytest.mark.parametrize("module", MODULE_PATHS)
def test_module_execution_rejects_an_unknown_command(module):
    result = _run_module(module, "no-such-command")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert result.stdout == ""
