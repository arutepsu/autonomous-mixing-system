"""R5C increment C1: strict bounded run-file reading, the allowlisted run configuration, the typed request factory and
the pre-write runtime check. No CLI activation; labelled numerical fixtures only (not evaluation data)."""

import copy
import dataclasses
import json
import math
import os
from pathlib import Path

import pytest
from agent_runtime_support import LIMIT, Setup, provider_config
from masking_diagnosis_support import CURRENT_SCREEN
from run_config_support import expected_request, run_config_data, write_run_config

from autonomous_mixing.adapters.journal.record_codec import encode
from autonomous_mixing.core.records.identifiers import RunId, VersionRef
from autonomous_mixing.drivers import daw_runtime
from autonomous_mixing.drivers.daw_runtime import (
    MAX_RUN_CONFIG_BYTES,
    MAX_RUN_CONFIG_DEPTH,
    PROVISIONAL_ANALYSIS_SPEC,
    REVIEWED_EXPERIMENTAL_POLICY,
    ConfigError,
    load_run_config,
    read_strict_json,
)

RUN = RunId("run-1")
EXAMPLE = Path(__file__).resolve().parents[2] / "docs/superpowers/plans/2026-11-24-r5c-run-config-example.json"


def load(setup, data=None, *, text=None):
    return load_run_config(write_run_config(setup, data, text=text), setup.config)


def refused(setup, data, match):
    path = write_run_config(setup, data)
    before = sorted(str(p) for p in setup.root.rglob("*"))
    with pytest.raises(ConfigError, match=match):
        load_run_config(path, setup.config)
    assert sorted(str(p) for p in setup.root.rglob("*")) == before


def plain_paths(data):
    """Every allowlisted plain-envelope object (never inside a codec value) and its key path."""
    run = data["run"]
    return [
        ((), data),
        (("run",), run),
        (("run", "daw_session"), run["daw_session"]),
        (("run", "definitions", 0), run["definitions"][0]),
        (("run", "measurements", 0), run["measurements"][0]),
        (("preparation",), data["preparation"]),
        (("provider",), data["provider"]),
        (("limits",), data["limits"]),
    ]


def at(data, path):
    for part in path:
        data = data[part]
    return data


def label(path, key):
    return ".".join(str(p) for p in (*path, key))


# --- strict bounded JSON reading (review correction 3) --------------------------------------------------------------


def test_the_bounds_are_resource_limits_covering_the_example_with_headroom():
    assert REVIEWED_EXPERIMENTAL_POLICY == CURRENT_SCREEN
    example = read_strict_json(EXAMPLE)

    def depth(value):
        children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
        return 1 + max((depth(c) for c in children), default=0) if isinstance(value, (dict, list)) else 0

    assert depth(example) == 14 < MAX_RUN_CONFIG_DEPTH
    assert EXAMPLE.stat().st_size * 100 < MAX_RUN_CONFIG_BYTES


def test_an_oversized_file_is_refused_after_a_bounded_read(tmp_path, monkeypatch):
    path = tmp_path / "big.json"
    path.write_bytes(b'{"a": "' + b"x" * 5000 + b'"}')
    read = []
    original = os.read

    def spy(descriptor, size):
        chunk = original(descriptor, size)
        read.append(len(chunk))
        return chunk

    monkeypatch.setattr(daw_runtime.os, "read", spy)
    with pytest.raises(ConfigError, match="larger than 100 bytes"):
        read_strict_json(path, max_bytes=100)
    assert sum(read) <= 101
    assert read_strict_json(path, max_bytes=6000) == {"a": "x" * 5000}


def test_a_link_is_refused(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"the OS refuses to create a symbolic link here: {error}")
    with pytest.raises(ConfigError, match="regular file"):
        read_strict_json(link)


def test_a_directory_or_missing_file_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="regular file"):
        read_strict_json(tmp_path)
    with pytest.raises(ConfigError, match="cannot read"):
        read_strict_json(tmp_path / "missing.json")


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        (b'{"a": "\xff"}', "UTF-8"),
        (b'\xef\xbb\xbf{"a": 1}', "byte-order mark"),
        (b'{"a": 1, "a": 2}', "duplicate key 'a'"),
        (b'{"a": {"b": 1, "b": 1}}', "duplicate key 'b'"),
        (b'{"a": {"$t": "x", "f": {"v": 1, "v": 1}}}', "duplicate key 'v'"),
        (b'{"a": NaN}', "non-finite"),
        (b'{"a": Infinity}', "non-finite"),
        (b'{"a": -Infinity}', "non-finite"),
        (b'{"a": 1e400}', "non-finite"),
        (b'{"a": -1e400}', "non-finite"),
        (b'{"a": 123456789012345678901}', "more than 20 digits"),
        (b'{"a": -123456789012345678901}', "more than 20 digits"),
        (b'{"a": ' + b"9" * 5000 + b"}", "more than 20 digits"),
        (b"[" * 65 + b"]" * 65, "nested deeper than 64"),
        (b'{"a": 1', "invalid JSON"),
        (b"", "invalid JSON"),
    ],
)
def test_malformed_or_hazardous_json_is_refused(tmp_path, raw, match):
    path = tmp_path / "c.json"
    path.write_bytes(raw)
    with pytest.raises(ConfigError, match=match):
        read_strict_json(path)


def test_the_depth_scan_counts_structure_only(tmp_path):
    path = tmp_path / "c.json"
    path.write_bytes(b"[" * 64 + b"]" * 64)
    assert read_strict_json(path) is not None
    path.write_text(json.dumps({"a": "[" * 200, "b": '\\"{' * 200, "c": "\\" + "[" * 200}), encoding="utf-8")
    assert read_strict_json(path)["a"] == "[" * 200


def test_large_integers_are_refused_without_converting_them(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    path.write_bytes(b'{"a": ' + b"7" * 5000 + b"}")
    with pytest.raises(ConfigError, match="more than 20 digits"):
        read_strict_json(path)
    path.write_bytes(b'{"a": 12345678901234567890, "b": -0.0}')
    loaded = read_strict_json(path)
    assert loaded["a"] == 12345678901234567890 and math.copysign(1.0, loaded["b"]) == -1.0


# --- the allowlisted envelope ----------------------------------------------------------------------------------------


def test_the_example_shaped_fixture_loads_into_typed_inputs(tmp_path):
    setup = Setup(tmp_path)
    loaded = load(setup)
    assert loaded.preparation == setup.preparation
    assert loaded.provider == provider_config()
    assert (loaded.max_evidence_bytes, loaded.max_audit_record_bytes) == (LIMIT, LIMIT)
    assert loaded.known_alignments == frozenset({VersionRef("alignment.common_export_selection", "1")})


def test_every_allowlisted_key_is_required(tmp_path):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    for path, obj in plain_paths(data):
        for key in obj:
            changed = copy.deepcopy(data)
            del at(changed, path)[key]
            refused(setup, changed, f"{label(path, key)}: missing")


@pytest.mark.parametrize("extra", ["comment", "defaults", "api_key"])
def test_an_unknown_key_is_refused_at_every_envelope_level(tmp_path, extra):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    for path, _ in plain_paths(data):
        changed = copy.deepcopy(data)
        at(changed, path)[extra] = 1
        refused(setup, changed, f"{label(path, extra)}: unknown key")


@pytest.mark.parametrize(
    ("path", "key"),
    [
        (("run",), "analysis_spec"),
        (("run",), "analysis_definitions"),
        (("run",), "output_root_key"),
        (("run",), "provider_binding"),
        (("run",), "configuration"),
        (("run", "daw_session"), "profile"),
        (("preparation",), "qualification"),
        (("preparation",), "max_file_bytes"),
        (("preparation",), "state"),
        (("preparation",), "root_bindings"),
        ((), "read_limits"),
    ],
)
def test_derived_fields_cannot_be_declared(tmp_path, path, key):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    at(data, path)[key] = 1
    refused(setup, data, f"{label(path, key)}: unknown key")


@pytest.mark.parametrize("name", ["hidden_reference", "evaluation", "ground_truth", "clean_mix", "target_mix_wav",
                                  "injected_defect", "Reference"])  # fmt: skip
def test_evaluation_or_hidden_inputs_are_refused_by_name_at_every_level(tmp_path, name):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    for path, _ in plain_paths(data):
        changed = copy.deepcopy(data)
        at(changed, path)[name] = "x"
        refused(setup, changed, "evaluation/hidden inputs are not runtime configuration")


@pytest.mark.parametrize(("key", "value"), [("schema", 2), ("schema", "1"), ("schema", True),
                                            ("kind", "autonomous_mixing.daw_tool")])  # fmt: skip
def test_the_envelope_version_and_kind_are_exact(tmp_path, key, value):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    data[key] = value
    refused(setup, data, key)


def test_the_tool_configuration_is_not_a_run_file(tmp_path):
    setup = Setup(tmp_path)
    with pytest.raises(ConfigError, match="profile_reference: evaluation/hidden inputs"):
        load_run_config(setup.config.path, setup.config)


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("run", "initial_state"), "render_spec", "run.initial_state: must be a ProjectState"),
        (("run", "criteria"), "scope", "run.criteria: must be a AcceptanceCriteria"),
        (("run", "root_bindings"), "scope", "run.root_bindings: must be a nonempty list"),
        (("run", "daw_session", "binding"), "scope", "run.daw_session.binding: must be a DawTrackBinding"),
        (("preparation", "jobs"), "[render]", "preparation.jobs: every item must be a MaskingPreparationJob"),
        (("run", "scope"), {"$t": "observation.Nowhere", "f": {}}, "run.scope: invalid record"),
        (("run", "scope"), {"$t": "observation.AnalysisScope", "f": {}}, "run.scope: invalid record"),
    ],
)
def test_codec_values_must_decode_to_their_declared_type(tmp_path, path, value, match):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    if value == "render_spec" or value == "scope":
        value = data["run"][value]
    elif value == "[render]":
        value = [data["run"]["render_spec"]]
    at(data, path[:-1])[path[-1]] = value
    refused(setup, data, match)


@pytest.mark.parametrize(
    "field",
    ["energy_above_noise_db", "quiet_elevation_db", "margin_deadband_db", "minimum_robust_fraction",
     "minimum_cells", "minimum_consecutive_calls"],
)  # fmt: skip
def test_only_the_reviewed_experimental_policy_is_accepted(tmp_path, field):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    fields = data["preparation"]["experimental_policy"]["f"]
    if isinstance(fields[field], int):
        fields[field] += 1
    else:
        fields[field] = math.nextafter(fields[field], math.inf)
    refused(setup, data, "preparation.experimental_policy: must equal the reviewed experimental policy")


def test_another_screening_method_is_refused_by_the_policy_record_itself(tmp_path):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    data["preparation"]["experimental_policy"]["f"]["method"] = encode(VersionRef("p1.experimental_screen", "2"))
    refused(setup, data, "preparation.experimental_policy: invalid record")


def test_declared_definitions_use_the_level_analyzer_only(tmp_path):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    data["run"]["definitions"][0]["analyzer"] = encode(VersionRef("autonomous_mixing.p1_directional_masking", "1"))
    refused(setup, data, r"run.definitions.0.analyzer: must be the level analyzer")


def test_definition_names_are_unique_and_measurements_name_a_declared_one(tmp_path):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    data["run"]["definitions"][1]["name"] = data["run"]["definitions"][0]["name"]
    refused(setup, data, "run.definitions.1.name: duplicate")
    data = run_config_data(setup)
    data["run"]["measurements"][0]["definition"] = "undeclared"
    refused(setup, data, "run.measurements.0.definition: names no declared definition")


def test_roots_come_only_from_the_tool_configuration(tmp_path):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    data["run"]["root_bindings"][0]["f"]["root_key"] = "elsewhere"
    refused(setup, data, "run.root_bindings: root key 'elsewhere' is not a configured root")
    data = run_config_data(setup)
    data["run"]["daw_session"]["export_root_key"] = "elsewhere"
    refused(setup, data, "run.daw_session.export_root_key: 'elsewhere' is not a configured root")


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("limits", "max_evidence_bytes"), True, "limits.max_evidence_bytes: must be a positive integer"),
        (("limits", "max_audit_record_bytes"), 0, "limits.max_audit_record_bytes: must be a positive integer"),
        (("preparation", "max_elapsed_seconds"), True, "preparation.max_elapsed_seconds: must be a positive"),
        (("preparation", "max_elapsed_seconds"), 0, "preparation.max_elapsed_seconds: must be a positive"),
        (("preparation", "known_alignments"), [], "preparation.known_alignments: must be a nonempty list"),
        (("provider", "max_tokens"), True, "provider: "),
        (("provider", "temperature"), 2.0, "provider: "),
        (("provider", "api_version"), "2099-01-01", "provider: "),
        (("run", "definitions", 0, "unit"), " ", "run.definitions.0.unit: must be nonblank text"),
    ],
)
def test_plain_values_are_strictly_typed(tmp_path, path, value, match):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    at(data, path[:-1])[path[-1]] = value
    refused(setup, data, match)


def test_duplicate_known_alignments_are_refused(tmp_path):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    data["preparation"]["known_alignments"] *= 2
    refused(setup, data, "preparation.known_alignments: duplicate")


def test_strict_reading_applies_to_the_run_file(tmp_path):
    setup = Setup(tmp_path)
    text = json.dumps(run_config_data(setup))
    with pytest.raises(ConfigError, match="duplicate key 'schema'"):
        load(setup, text='{"schema": 1, ' + text[1:])


# --- provisional validation and the prepared factory -----------------------------------------------------------------


def test_the_provisional_request_reproduces_the_typed_fixture_exactly(tmp_path):
    setup = Setup(tmp_path)
    provisional = load(setup).provisional_request(RUN, setup.config)
    assert provisional == expected_request(setup, PROVISIONAL_ANALYSIS_SPEC)
    assert provisional.provider_binding is None


def test_structural_run_errors_are_refused_by_the_provisional_request(tmp_path):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    del data["run"]["measurements"][0]
    loaded = load(setup, data)
    with pytest.raises(ConfigError, match="run: "):
        loaded.provisional_request(RUN, setup.config)


def test_the_prepared_factory_is_accepted_by_create_and_equals_the_typed_fixture(tmp_path):
    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    setup = Setup(tmp_path)
    loaded = load(setup)
    with DawAgentRuntime.create(
        config=setup.config,
        run_id=RUN,
        preparation=loaded.preparation,
        request_factory=loaded.request_factory(RUN, setup.config),
        provider_config=loaded.provider,
        api_key="unit-test-only",
        max_evidence_bytes=loaded.max_evidence_bytes,
        max_audit_record_bytes=loaded.max_audit_record_bytes,
        known_alignments=loaded.known_alignments,
        clock=setup.clock,
        identities=setup.identities,
        provider_transport_factory=lambda config, key: setup.provider,
        fl_transport_factory=setup.fl_factory,
    ) as runtime:
        policy = runtime.request.provider_binding.diagnosis_policy
        plan = policy.measurement_plan
        diagnostics = tuple(d for job in policy.jobs for d in job.measurements)
        assert diagnostics and plan.specification != PROVISIONAL_ANALYSIS_SPEC
        assert plan.prepared.profile == loaded.preparation.profile
        expected = expected_request(setup, plan.specification, diagnostics)
        assert dataclasses.replace(runtime.request, provider_binding=None) == expected
        assert not set(diagnostics) & {m.definition for m in runtime.request.measurements}
    assert setup.provider.calls == [] and setup.world is None


# --- typed runtime refusals and the pre-write check ------------------------------------------------------------------


def test_runtime_state_refusals_are_typed_and_remain_value_errors(tmp_path):
    from autonomous_mixing.drivers.agent_runtime import RuntimeRefusal

    assert issubclass(RuntimeRefusal, ValueError)
    setup = Setup(tmp_path)
    (setup.config.run_store_dir / "run-1.runtime").mkdir()
    with pytest.raises(RuntimeRefusal, match="already exist"):
        setup.create()


def test_reopen_drift_is_a_typed_refusal(tmp_path):
    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime, RuntimeRefusal

    setup = Setup(tmp_path)
    with setup.create() as runtime:
        runtime.start()
    with pytest.raises(RuntimeRefusal, match="drift"):
        DawAgentRuntime.reopen(config=setup.config, run_id=setup.request_id,
                               provider_config=dataclasses.replace(provider_config(), max_tokens=7),
                               api_key="unit-test-only", clock=setup.clock, identities=setup.identities,
                               provider_transport_factory=lambda *a: setup.provider,
                               fl_transport_factory=setup.fl_factory)  # fmt: skip


def test_the_pre_write_check_accepts_a_valid_declaration_and_creates_nothing(tmp_path):
    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    setup = Setup(tmp_path)
    loaded = load(setup)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    DawAgentRuntime.check(config=setup.config, request=loaded.provisional_request(RUN, setup.config),
                          preparation=loaded.preparation)  # fmt: skip
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("field", ["frame_count", "start_seconds", "sample_rate_hz", "render_spec"])
def test_the_pre_write_check_refuses_native_export_mismatches_before_any_directory(tmp_path, field):
    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    setup = Setup(tmp_path)
    data = run_config_data(setup)
    session = data["run"]["daw_session"]
    if field == "render_spec":
        session["render_spec"] = encode(VersionRef("another-render", "1"))
    else:
        timeline = session["export_timeline"]["f"]
        timeline[field] = {"frame_count": timeline["frame_count"] + 1, "start_seconds": 1.0,
                           "sample_rate_hz": 48000}[field]  # fmt: skip
    loaded = load(setup, data)
    with pytest.raises(ValueError, match="native export"):
        DawAgentRuntime.check(config=setup.config, request=loaded.provisional_request(RUN, setup.config),
                              preparation=loaded.preparation)  # fmt: skip
    assert not list(setup.config.run_store_dir.iterdir())
    assert not setup.config.fl_journal.exists()


def test_the_pre_write_check_refuses_a_bound_or_foreign_request(tmp_path):
    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    setup = Setup(tmp_path)
    loaded = load(setup)
    provisional = loaded.provisional_request(RUN, setup.config)
    other = dataclasses.replace(loaded.preparation, max_elapsed_seconds=1.0, max_file_bytes=1)
    with pytest.raises(ValueError, match="read limits"):
        DawAgentRuntime.check(config=setup.config, request=provisional, preparation=other)
    with setup.create() as runtime:
        bound = runtime.request
    with pytest.raises(ValueError, match="provider binding"):
        DawAgentRuntime.check(config=setup.config, request=bound, preparation=loaded.preparation)


# --- review fixes (2026-11-24): expected I/O failures are typed; descriptors close; programming errors propagate -----


@pytest.mark.parametrize("failing", ["fstat", "read"])
def test_an_io_failure_while_reading_is_a_configuration_error_and_closes_the_descriptor(tmp_path, monkeypatch, failing):
    path = tmp_path / "c.json"
    path.write_text("{}", encoding="utf-8")
    opened, closed = [], []
    real_open, real_close = os.open, os.close

    def track_open(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def track_close(descriptor):
        closed.append(descriptor)
        real_close(descriptor)

    def broken(*args, **kwargs):
        raise OSError(5, "injected input/output error")

    monkeypatch.setattr(daw_runtime.os, "open", track_open)
    monkeypatch.setattr(daw_runtime.os, "close", track_close)
    monkeypatch.setattr(daw_runtime.os, failing, broken)
    with pytest.raises(ConfigError, match="cannot read .*injected input/output error"):
        read_strict_json(path)
    assert opened and closed == opened


def test_a_programming_error_while_reading_propagates_unchanged_and_closes_the_descriptor(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    path.write_text("{}", encoding="utf-8")
    closed = []
    real_close = os.close

    def track_close(descriptor):
        closed.append(descriptor)
        real_close(descriptor)

    def broken(*args, **kwargs):
        raise TypeError("injected programming error")

    monkeypatch.setattr(daw_runtime.os, "close", track_close)
    monkeypatch.setattr(daw_runtime.os, "read", broken)
    with pytest.raises(TypeError, match="injected programming error"):
        read_strict_json(path)
    assert len(closed) == 1


def _reopen(setup, opened):
    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    return DawAgentRuntime.reopen(config=setup.config, run_id=setup.request_id, provider_config=provider_config(),
                                  api_key="unit-test-only", clock=setup.clock, identities=setup.identities,
                                  provider_transport_factory=lambda cfg, key: opened.append(cfg) or setup.provider,
                                  fl_transport_factory=setup.fl_factory)  # fmt: skip


@pytest.mark.parametrize("damage", ["missing", "altered", "unreadable"])
def test_an_unusable_stored_manifest_is_a_state_refusal_and_never_regenerated(tmp_path, damage):
    from autonomous_mixing.drivers.agent_runtime import RuntimeRefusal

    setup = Setup(tmp_path)
    with setup.create() as runtime:
        runtime.start()
    plans = setup.config.run_store_dir / "run-1.runtime" / "plans"
    (manifest,) = plans.glob("*.json")
    if damage == "missing":
        manifest.unlink()
    elif damage == "altered":
        manifest.write_bytes(manifest.read_bytes() + b" ")
    else:
        manifest.unlink()
        manifest.mkdir()
    listing = sorted(p.name for p in plans.iterdir())
    before = list(setup.world.sent)
    opened = []
    with pytest.raises(RuntimeRefusal, match="stored preparation manifest is unusable"):
        _reopen(setup, opened)
    assert opened == [] and setup.world.sent == before
    assert sorted(p.name for p in plans.iterdir()) == listing


def test_a_stored_manifest_decoding_to_another_plan_is_a_state_refusal(tmp_path, monkeypatch):
    from autonomous_mixing.drivers import agent_runtime
    from autonomous_mixing.drivers.agent_runtime import RuntimeRefusal

    setup = Setup(tmp_path)
    with setup.create() as runtime:
        runtime.start()
    monkeypatch.setattr(agent_runtime, "load_prepared_plan", lambda store, digest: object())  # decodes, differs
    before, opened = list(setup.world.sent), []
    with pytest.raises(RuntimeRefusal, match="stored preparation manifest is unusable: it differs from the run"):
        _reopen(setup, opened)
    assert opened == [] and setup.world.sent == before
