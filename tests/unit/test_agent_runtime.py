"""R5B composition: real internals, external transports only replaced. No live services."""

import dataclasses
import json

import pytest
from test_r5a_decision_evidence import binding

from autonomous_mixing.adapters.journal.record_codec import decode, dumps, encode, loads
from autonomous_mixing.core.records.provider_run import DawAgentBinding


def test_local_runtime_binding_is_additive_and_exactly_serializable():
    base = binding()
    fields = {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}
    configured = DawAgentBinding(**fields, runtime_settings='{"schema":1}')
    assert isinstance(configured, type(base))
    assert decode(loads(dumps(encode(configured)))) == configured
    assert [f.name for f in dataclasses.fields(type(base))] == [
        "provider",
        "prompt",
        "response_schema",
        "diagnosis_policy",
    ]


@pytest.mark.parametrize(
    "settings", ["{} ", "[]", '{"api_key":"secret"}', '{"schema":NaN}', '{"nested":{"authorization":"secret"}}']
)
def test_runtime_binding_refuses_noncanonical_or_credential_settings(settings):
    base = binding()
    fields = {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}
    with pytest.raises(ValueError):
        DawAgentBinding(**fields, runtime_settings=settings)


def test_real_internal_components_run_to_verified_suspension_and_acceptance(tmp_path):
    from agent_runtime_support import Setup

    from autonomous_mixing.core.records.run_report import RunSuspended
    from autonomous_mixing.core.records.termination import TerminationReason

    setup = Setup(tmp_path)
    with setup.create() as runtime:
        suspended = runtime.start()
        assert isinstance(suspended, RunSuspended), suspended
        assert len(setup.provider.calls) == 1
        assert suspended.request.provider_binding == runtime.request.provider_binding
        public = json.dumps(setup.provider.calls).replace("\\\\", "\\")
        assert str(tmp_path) in json.loads(runtime.request.provider_binding.runtime_settings)["run_store_dir"]
        assert str(tmp_path) not in public
        assert "unit-test-only" not in public
        assert all(
            "unit-test-only" not in p.read_bytes().decode("utf-8", errors="ignore")
            for p in setup.config.run_store_dir.rglob("*")
            if p.is_file()
        )
        report = runtime.continue_export(suspended, setup.export(suspended))
        assert report.final_run.termination_reason is TerminationReason.CANDIDATE_CRITERIA_MET
        assert report.verified == report.authoritative and report.authoritative.sequence == 1
    assert setup.provider.closed


def test_initial_inconclusive_stops_without_a_model_or_device_write(tmp_path):
    from agent_runtime_support import Setup

    from autonomous_mixing.core.records.termination import TerminationReason

    setup = Setup(tmp_path, silent=True)
    with setup.create() as runtime:
        report = runtime.start()
        assert report.final_run.termination_reason is TerminationReason.INITIAL_ASSESSMENT_INCONCLUSIVE
        assert setup.provider.calls == []
        assert setup.world is None  # no MIDI transport was constructed


def test_initial_success_preserves_the_existing_no_decision_gate(tmp_path):
    from agent_runtime_support import Setup

    from autonomous_mixing.core.records.observation import MeasuredScalar
    from autonomous_mixing.core.records.termination import TerminationReason

    setup = Setup(tmp_path)
    original = setup.request

    def already_met(plan, diagnostics):
        request = original(plan, diagnostics)
        criteria = request.configuration.criteria
        target = dataclasses.replace(criteria.targets[0], threshold=MeasuredScalar(0.0, "dBFS"))
        return dataclasses.replace(
            request,
            configuration=dataclasses.replace(
                request.configuration, criteria=dataclasses.replace(criteria, targets=(target,))
            ),
        )

    setup.request = already_met
    with setup.create() as runtime:
        report = runtime.start()
        assert report.final_run.termination_reason is TerminationReason.INITIAL_CRITERIA_MET
        assert setup.provider.calls == []


def test_restart_rehydrates_frozen_settings_and_does_not_resend(tmp_path):
    from agent_runtime_support import ProviderTransport, Setup, provider_config

    from autonomous_mixing.core.records.termination import TerminationReason
    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    setup = Setup(tmp_path)
    before = {p.name: p.read_bytes() for p in (tmp_path / "inputs").glob("*.wav")}
    with setup.create() as runtime:
        suspended = runtime.start()
        original = runtime.request
    assert setup.provider.closed
    setup.provider = ProviderTransport()
    with DawAgentRuntime.reopen(
        config=setup.config,
        run_id=setup.request_id,
        provider_config=provider_config(),
        api_key="unit-test-only",
        clock=setup.clock,
        identities=setup.identities,
        provider_transport_factory=lambda cfg, key: setup.provider,
        fl_transport_factory=setup.fl_factory,
    ) as reopened:
        assert reopened.request == original
        assert reopened.suspended == suspended
        report = reopened.continue_export(reopened.suspended, setup.export(reopened.suspended))
        assert report.final_run.termination_reason is TerminationReason.CANDIDATE_CRITERIA_MET
        assert setup.provider.calls == []
    assert len([m for m in setup.world.sent if m["type"] == "set_gain_db"]) == 1
    assert {p.name: p.read_bytes() for p in (tmp_path / "inputs").glob("*.wav")} == before


def test_configuration_drift_is_refused_before_constructing_external_transports(tmp_path):
    from agent_runtime_support import Setup, provider_config

    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    setup = Setup(tmp_path)
    with setup.create() as runtime:
        runtime.start()
    opened = []
    with pytest.raises(ValueError, match="drift"):
        DawAgentRuntime.reopen(
            config=setup.config,
            run_id=setup.request_id,
            provider_config=dataclasses.replace(provider_config(), model="other"),
            api_key="unit-test-only",
            provider_transport_factory=lambda *a: opened.append(a),
            fl_transport_factory=lambda *a: opened.append(a),
        )
    assert opened == []


def test_missing_evidence_refuses_restart_without_provider_or_device_dispatch(tmp_path):
    from agent_runtime_support import Setup, provider_config

    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    setup = Setup(tmp_path)
    with setup.create() as runtime:
        runtime.start()
    next((setup.config.run_store_dir / "run-1.runtime" / "evidence").glob("*.json")).unlink()
    before = list(setup.world.sent)
    with pytest.raises(ValueError, match="evidence"):
        DawAgentRuntime.reopen(
            config=setup.config,
            run_id=setup.request_id,
            provider_config=provider_config(),
            api_key="unit-test-only",
            clock=setup.clock,
            identities=setup.identities,
            provider_transport_factory=lambda cfg, key: setup.provider,
            fl_transport_factory=setup.fl_factory,
        )
    assert setup.world.sent == before
    assert len(setup.provider.calls) == 1


@pytest.mark.parametrize("case", ["small_support", "below_fraction"])
def test_real_diagnosis_can_abstain_without_any_write(tmp_path, case):
    from agent_runtime_support import Setup

    from autonomous_mixing.core.records.masking_measurement import BandRegion
    from autonomous_mixing.core.records.termination import TerminationReason

    setup = (
        Setup(tmp_path, region=BandRegion("test-band", (5,)))
        if case == "small_support"
        else Setup(tmp_path, target_counts=32767)
    )
    with setup.create() as runtime:
        report = runtime.start()
        assert report.final_run.termination_reason is TerminationReason.INSUFFICIENT_EVIDENCE
        assert len(setup.provider.calls) == 1
        assert setup.provider.calls[0]["diagnosis"]["problem_candidates"] == []
        assert setup.world is None


@pytest.mark.parametrize("mode", ["malformed", "refused", "timeout"])
def test_real_provider_adapter_failures_never_become_gain_writes(tmp_path, mode):
    from agent_runtime_support import Setup

    setup = Setup(tmp_path)
    setup.provider.mode = mode
    with setup.create() as runtime:
        report = runtime.start()
        assert report.final_run.usage.attempts == 0
        assert report.final_run.usage.decision_calls == len(setup.provider.calls)
        assert setup.world is None
        assert report.final_run.outcome.value != "succeeded"


def test_structurally_valid_but_denied_proposal_is_not_applied(tmp_path):
    from agent_runtime_support import Setup

    setup = Setup(tmp_path, gains=(2.5,))
    with setup.create() as runtime:
        report = runtime.start()
        assert report.final_run.usage.attempts == 1
        assert setup.world is None
        assert report.authoritative.sequence == 0


def test_guard_rejected_export_is_restored_then_feedback_precedes_a_revised_decision(tmp_path):
    from agent_runtime_support import Setup

    from autonomous_mixing.core.records.observation import MeasuredScalar
    from autonomous_mixing.core.records.run_report import RunSuspended
    from autonomous_mixing.core.records.termination import TerminationReason

    setup = Setup(tmp_path, gains=(-1.0, 0.0))
    original = setup.request

    def stricter_improvement(plan, definitions):
        from autonomous_mixing.core.records.identifiers import ConditionId
        from autonomous_mixing.core.records.verification import BaselineReference, DeltaGuard
        from autonomous_mixing.core.records.verification_assessment import ConditionMeasurement

        request = original(plan, definitions)
        criteria = request.configuration.criteria
        guard = DeltaGuard(
            ConditionId("stem-guard"),
            criteria.targets[0].selector,
            BaselineReference.INITIAL,
            MeasuredScalar(2.5, "dBFS"),
        )
        measurement = ConditionMeasurement(guard.condition_id, request.measurements[0].definition)
        return dataclasses.replace(
            request,
            configuration=dataclasses.replace(
                request.configuration, criteria=dataclasses.replace(criteria, guards=(*criteria.guards, guard))
            ),
            measurements=(*request.measurements, measurement),
        )

    setup.request = stricter_improvement
    with setup.create() as runtime:
        first = runtime.start()
        second = runtime.continue_export(first, setup.export(first))
        assert isinstance(second, RunSuspended), second
        assert len(setup.provider.calls) == 2
        assert setup.provider.calls[1]["history"]
        assert len([m for m in setup.world.sent if m["type"] == "restore_gain"]) == 1
        result = runtime.continue_export(second, setup.export(second))
        assert result.final_run.termination_reason is TerminationReason.CANDIDATE_CRITERIA_MET


def test_invalid_export_profile_refuses_before_constructing_external_transports(tmp_path):
    from agent_runtime_support import Setup

    from autonomous_mixing.core.records.daw_export import DawExportFormat

    setup = Setup(tmp_path)
    original = setup.request

    def wrong_encoding(plan, defs):
        request = original(plan, defs)
        return dataclasses.replace(
            request,
            daw_session=dataclasses.replace(request.daw_session, export_format=DawExportFormat("WAV", "PCM_24")),
        )

    setup.request = wrong_encoding
    with pytest.raises(ValueError, match="encoding"):
        setup.create()
    assert setup.provider.calls == [] and setup.world is None
    assert not setup.config.fl_journal.exists()


def test_partial_construction_closes_all_opened_stores_without_dispatch(tmp_path):
    from agent_runtime_support import ALIGNMENT, LIMIT, Setup, provider_config

    from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
    from autonomous_mixing.adapters.journal.sqlite_run import SqliteRunJournal
    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    setup = Setup(tmp_path)

    def fail(*args):
        raise RuntimeError("injected transport construction error")

    with pytest.raises(RuntimeError, match="injected"):
        DawAgentRuntime.create(
            config=setup.config,
            run_id=setup.request_id,
            preparation=setup.preparation,
            request_factory=setup.request,
            provider_config=provider_config(),
            api_key="unit-test-only",
            max_evidence_bytes=LIMIT,
            max_audit_record_bytes=LIMIT,
            known_alignments=frozenset({ALIGNMENT.method}),
            clock=setup.clock,
            identities=setup.identities,
            provider_transport_factory=fail,
            fl_transport_factory=setup.fl_factory,
        )
    store = SqliteRunJournal.open(setup.config.store_path(setup.request_id.value))
    store.close()
    fl = SqliteCommandJournal(setup.config.fl_journal, session_key=setup.config.session_key)
    fl.close()
    assert setup.world is None


def test_create_never_overwrites_existing_run_or_reprepares_on_retry(tmp_path):
    from agent_runtime_support import Setup

    setup = Setup(tmp_path)
    with setup.create():
        files = {
            str(p): p.read_bytes() for p in (setup.config.run_store_dir / "run-1.runtime" / "plans").glob("*.json")
        }
        with pytest.raises(ValueError, match="already exist"):
            setup.create()
        assert files == {
            str(p): p.read_bytes() for p in (setup.config.run_store_dir / "run-1.runtime" / "plans").glob("*.json")
        }
    assert setup.provider.calls == [] and setup.world is None


def test_accepted_intermediate_state_uses_current_inventory_for_next_export(tmp_path):
    from agent_runtime_support import Setup

    from autonomous_mixing.core.records.masking_measurement import BandRegion
    from autonomous_mixing.core.records.observation import MeasuredScalar
    from autonomous_mixing.core.records.run_report import RunSuspended
    from autonomous_mixing.core.records.termination import TerminationReason

    # A predeclared long, single main-tone-band numerical fixture keeps >=100 high-energy cells.
    setup = Setup(tmp_path, frames=69120, region=BandRegion("main-tone-test-band", (5,)), gains=(0.0, -2.0))
    original = setup.request

    def later_target(plan, definitions):
        request = original(plan, definitions)
        criteria = request.configuration.criteria
        target = dataclasses.replace(criteria.targets[0], threshold=MeasuredScalar(-15.8, "dBFS"))
        return dataclasses.replace(
            request,
            configuration=dataclasses.replace(
                request.configuration, criteria=dataclasses.replace(criteria, targets=(target,))
            ),
        )

    setup.request = later_target
    with setup.create() as runtime:
        first = runtime.start()
        second = runtime.continue_export(first, setup.export(first))
        assert isinstance(second, RunSuspended), second
        assert second.checkpoint_event.payload.accepted.sequence == 1
        assert second.checkpoint_event.payload.before != first.checkpoint_event.payload.before
        assert len(setup.provider.calls) == 2
        result = runtime.continue_export(second, setup.export(second))
        assert result.final_run.termination_reason is TerminationReason.CANDIDATE_CRITERIA_MET
        assert result.authoritative.sequence == 2
        assert "uncalibrated" in json.dumps(setup.provider.calls)
        assert str(tmp_path) not in json.dumps(setup.provider.calls)


def test_lost_apply_reply_recovers_exactly_through_real_composition(tmp_path):
    from agent_runtime_support import Setup

    from autonomous_mixing.adapters.fl_studio import protocol
    from autonomous_mixing.core.records.termination import TerminationReason

    setup = Setup(tmp_path)
    original = setup.fl_factory

    def lost_write(journal):
        transport = original(journal)
        send = transport.send

        def send_losing_apply(raw):
            if protocol.decode(raw)["type"] == "set_gain_db":
                setup.world.lose_next_reply = True
            send(raw)

        transport.send = send_losing_apply
        return transport

    setup.fl_factory = lost_write
    with setup.create() as runtime:
        report = runtime.start()
        assert report.final_run.termination_reason is TerminationReason.RENDER_FAILED
        assert setup.world.host.volumes[2] == 0.76
        assert not runtime.controller.control_state().value.unresolved
        assert len([m for m in setup.world.sent if m["type"] == "set_gain_db"]) == 1
        assert len([m for m in setup.world.sent if m["type"] == "restore_gain"]) == 1


def test_commit_ack_loss_keeps_accepted_edit_and_never_restores(tmp_path):
    import sqlite3

    from agent_runtime_support import Setup

    from autonomous_mixing.core.records.termination import TerminationReason

    setup = Setup(tmp_path)
    with setup.create() as runtime:
        suspended = runtime.start()
        commit = runtime.journal._commit
        lost = False

        def lose_acceptance_ack():
            nonlocal lost
            acceptance = runtime.journal._connection.execute("SELECT count(*) FROM acceptances").fetchone()[0]
            commit()
            if acceptance and not lost:
                lost = True
                raise sqlite3.OperationalError("labelled lost acceptance acknowledgement")

        runtime.journal._commit = lose_acceptance_ack
        report = runtime.continue_export(suspended, setup.export(suspended))
        assert lost
        assert report.authoritative.sequence == 1
        assert report.final_run.termination_reason is TerminationReason.CANDIDATE_CRITERIA_MET
        assert not [m for m in setup.world.sent if m["type"] == "restore_gain"]
        assert len(runtime.journal.snapshot().value.acceptances) == 1


def test_closed_runtime_cannot_dispatch(tmp_path):
    from agent_runtime_support import Setup

    setup = Setup(tmp_path)
    runtime = setup.create()
    runtime.close()
    runtime.close()
    with pytest.raises(ValueError, match="closed"):
        runtime.start()
    assert setup.world is None and setup.provider.calls == []


def test_nonidentical_stereo_candidate_is_unavailable_and_restored_without_fold_down(tmp_path):
    import hashlib
    from pathlib import Path

    import soundfile as sf
    from agent_runtime_support import Setup

    from autonomous_mixing.core.records.termination import TerminationReason

    setup = Setup(tmp_path, gains=(0.0,))
    with setup.create() as runtime:
        suspended = runtime.start()
        manifest = setup.export(suspended)
        changed = []
        for entry in manifest.files:
            path = Path(suspended.checkpoint_event.payload.awaiting.workspace_directory) / entry.file_name
            values, rate = sf.read(path, always_2d=True)
            values[:, 1] *= 0.5
            sf.write(path, values, rate, subtype="DOUBLE")
            changed.append(dataclasses.replace(entry, content_hash=hashlib.sha256(path.read_bytes()).hexdigest()))
        manifest = dataclasses.replace(manifest, files=tuple(changed))
        report = runtime.continue_export(suspended, manifest)
        assert report.authoritative.sequence == 0
        assert report.final_run.termination_reason is not TerminationReason.CANDIDATE_CRITERIA_MET
        assert setup.world.host.volumes[2] == 0.76
        assert len([m for m in setup.world.sent if m["type"] == "restore_gain"]) == 1


def test_missing_preparation_is_never_regenerated_on_restart(tmp_path):
    from agent_runtime_support import Setup, provider_config

    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    setup = Setup(tmp_path)
    with setup.create() as runtime:
        runtime.start()
    plans = setup.config.run_store_dir / "run-1.runtime" / "plans"
    for path in plans.glob("*.json"):
        path.unlink()
    before = list(setup.world.sent)
    opened = []
    with pytest.raises(ValueError):
        DawAgentRuntime.reopen(
            config=setup.config,
            run_id=setup.request_id,
            provider_config=provider_config(),
            api_key="unit-test-only",
            clock=setup.clock,
            identities=setup.identities,
            provider_transport_factory=lambda cfg, key: opened.append((cfg, key)) or setup.provider,
            fl_transport_factory=setup.fl_factory,
        )
    assert opened == []
    assert not list(plans.iterdir())
    assert setup.world.sent == before


@pytest.mark.parametrize("field", ["frame_count", "start_seconds", "sample_rate_hz", "render_spec"])
def test_incompatible_native_export_metadata_refuses_before_external_construction(tmp_path, field):
    from agent_runtime_support import Setup

    from autonomous_mixing.core.records.identifiers import VersionRef

    setup = Setup(tmp_path)
    original = setup.request

    def incompatible(plan, definitions):
        request = original(plan, definitions)
        session = request.daw_session
        if field == "render_spec":
            session = dataclasses.replace(session, render_spec=VersionRef("another-render", "1"))
        else:
            timeline = session.export_timeline
            value = {"frame_count": timeline.frame_count + 1, "start_seconds": 1.0, "sample_rate_hz": 48000}[field]
            session = dataclasses.replace(session, export_timeline=dataclasses.replace(timeline, **{field: value}))
        return dataclasses.replace(request, daw_session=session)

    setup.request = incompatible
    with pytest.raises(ValueError, match="native export"):
        setup.create()
    assert setup.world is None and setup.provider.calls == []
    assert not setup.config.fl_journal.exists()


def test_local_midi_settings_drift_refuses_before_external_construction(tmp_path):
    from agent_runtime_support import Setup, provider_config

    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime
    from autonomous_mixing.drivers.daw_runtime import load_config

    setup = Setup(tmp_path)
    with setup.create() as runtime:
        runtime.start()
    settings = json.loads(setup.config.path.read_text(encoding="utf-8"))
    settings["midi"]["output_port"] = "different-command-port"
    setup.config.path.write_text(json.dumps(settings), encoding="utf-8")
    changed = load_config(setup.config.path)
    opened = []
    before = list(setup.world.sent)
    with pytest.raises(ValueError, match="drift"):
        DawAgentRuntime.reopen(
            config=changed,
            run_id=setup.request_id,
            provider_config=provider_config(),
            api_key="unit-test-only",
            clock=setup.clock,
            identities=setup.identities,
            provider_transport_factory=lambda *args: opened.append(args),
            fl_transport_factory=setup.fl_factory,
        )
    assert opened == [] and setup.world.sent == before
