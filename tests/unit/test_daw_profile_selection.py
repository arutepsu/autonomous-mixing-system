"""Explicit DAW profile selection in the tool configuration and its stored-run compatibility (plan 2026-11-26).

Schema 1 keeps meaning the existing calibrated profile; schema 2 names one of exactly two profiles. A stored run is
refused under another selection before any provider or MIDI transport is constructed. Simulated only.
"""

import json
import re

import pytest
from test_daw_cli import write_config

from autonomous_mixing.adapters.fl_studio.journal import JournalError, SqliteCommandJournal
from autonomous_mixing.adapters.fl_studio.profile import calibrated_profile, ht01_calibrated_profile
from autonomous_mixing.drivers import daw_runtime
from autonomous_mixing.drivers.daw_runtime import ConfigError, load_config

HT01 = "ht01-live-calibration-v001"
EXISTING = "bass-bridge-test-v1"


def schema2(tmp_path, name, **overrides):
    return write_config(tmp_path, schema=2, daw_profile=name, **overrides)


def test_schema_1_keeps_meaning_the_existing_profile(tmp_path):
    config = load_config(write_config(tmp_path))
    assert config.daw_profile == EXISTING


@pytest.mark.parametrize("name", [EXISTING, HT01])
def test_schema_2_selects_exactly_the_named_profile(tmp_path, name):
    assert load_config(schema2(tmp_path, name)).daw_profile == name


@pytest.mark.parametrize(
    "overrides,key",
    [
        ({"schema": 2}, "daw_profile"),
        ({"schema": 2, "daw_profile": "ht01"}, "daw_profile"),
        ({"schema": 2, "daw_profile": 1}, "daw_profile"),
        ({"schema": 1, "daw_profile": HT01}, "daw_profile"),
        ({"schema": 3, "daw_profile": HT01}, "schema"),
        ({"schema": 2, "daw_profile": HT01, "ground_truth": "x"}, "ground_truth"),
        ({"schema": 2, "daw_profile": HT01, "reference_target_db": -1.0}, "reference_target_db"),
    ],
)
def test_invalid_profile_selection_names_the_key(tmp_path, overrides, key):
    with pytest.raises(ConfigError, match=re.escape(key)):
        load_config(write_config(tmp_path, **overrides))


def test_open_fl_uses_the_selected_profile(tmp_path):
    journal, controller = daw_runtime.open_fl(load_config(schema2(tmp_path, HT01)), read_only=False,
                                              transport_factory=lambda: None)  # fmt: skip
    try:
        assert controller.profile == ht01_calibrated_profile(reference=controller.profile.reference)
    finally:
        journal.close()
    journal, controller = daw_runtime.open_fl(load_config(write_config(tmp_path)), read_only=False,
                                              transport_factory=lambda: None)  # fmt: skip
    journal.close()
    assert controller.profile == calibrated_profile(reference=controller.profile.reference)


def test_open_fl_refuses_an_incompatible_journal_before_midi_and_releases_it(tmp_path):
    from test_fl_ht01_profile import record_existing_apply

    config = load_config(schema2(tmp_path, HT01))
    journal = SqliteCommandJournal(config.fl_journal, session_key=config.session_key)
    record_existing_apply(journal)
    journal.close()
    opened = []
    with pytest.raises(JournalError, match="scope"):
        daw_runtime.open_fl(config, read_only=False, transport_factory=lambda: opened.append(1))
    assert opened == []
    SqliteCommandJournal(config.fl_journal, session_key=config.session_key).close()  # not left locked
    journal, _ = daw_runtime.open_fl(load_config(write_config(tmp_path)), read_only=False,
                                     transport_factory=lambda: None)  # fmt: skip
    journal.close()  # the old journal keeps its meaning under the existing selection


# --- stored runs ------------------------------------------------------------------------------------------------------


def _rewrite(setup, **changes):
    settings = json.loads(setup.config.path.read_text(encoding="utf-8"))
    settings.update(changes)
    setup.config.path.write_text(json.dumps(settings), encoding="utf-8")
    return load_config(setup.config.path)


def _reopen(setup, config, opened):
    from agent_runtime_support import provider_config

    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    return DawAgentRuntime.reopen(
        config=config,
        run_id=setup.request_id,
        provider_config=provider_config(),
        api_key="unit-test-only",
        clock=setup.clock,
        identities=setup.identities,
        provider_transport_factory=lambda *args: opened.append(("provider", args)),
        fl_transport_factory=lambda *args: opened.append(("fl", args)),
    )


def test_stored_run_is_refused_under_another_profile_before_provider_or_midi(tmp_path):
    from agent_runtime_support import Setup

    setup = Setup(tmp_path)
    with setup.create() as runtime:
        runtime.start()
    sent = list(setup.world.sent)
    changed = _rewrite(setup, schema=2, daw_profile=HT01)
    opened = []
    with pytest.raises(ValueError, match="profile"):
        _reopen(setup, changed, opened)
    assert opened == [] and setup.world.sent == sent


def test_stored_run_keeps_its_meaning_under_an_explicit_existing_selection(tmp_path):
    from agent_runtime_support import ProviderTransport, Setup, provider_config

    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    setup = Setup(tmp_path)
    with setup.create() as runtime:
        suspended = runtime.start()
    changed = _rewrite(setup, schema=2, daw_profile=EXISTING)
    setup.provider = ProviderTransport()
    with DawAgentRuntime.reopen(
        config=changed,
        run_id=setup.request_id,
        provider_config=provider_config(),
        api_key="unit-test-only",
        clock=setup.clock,
        identities=setup.identities,
        provider_transport_factory=lambda cfg, key: setup.provider,
        fl_transport_factory=setup.fl_factory,
    ) as reopened:
        assert reopened.suspended == suspended
