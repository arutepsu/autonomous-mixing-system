"""Durable P1 evidence associations, using real temporary numerical WAVs only."""

import dataclasses
import json

import pytest
from test_experimental_masking import experimental_world, measure

from autonomous_mixing.core.records.analysis import AnalysisEvidenceReference, AnalysisResponse
from autonomous_mixing.core.records.identifiers import ObservationId, VersionRef
from autonomous_mixing.core.records.results import Success


def test_real_measurement_returns_restart_verifiable_associations(tmp_path):
    world = experimental_world(tmp_path)
    result = measure(world)
    assert isinstance(result, Success)
    response = result.value
    assert {r.observation_id for r in response.evidence} == {o.observation_id for o in response.observations}
    assert {r.prepared_digest for r in response.evidence} == {world.plan.prepared_digest}
    assert len({r.content_sha256 for r in response.evidence}) == 1
    world.make().verify_evidence(response)
    ref = response.evidence[0]
    path = world.evidence_dir / (ref.content_sha256 + ".json")
    doc = json.loads(path.read_bytes())
    assert doc["schema_version"] == 3
    path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="evidence"):
        world.make().verify_evidence(response)


def test_evidence_references_are_unique_and_only_for_returned_observations(tmp_path):
    response = measure(experimental_world(tmp_path)).value
    ref = response.evidence[0]
    with pytest.raises(ValueError):
        dataclasses.replace(response, evidence=response.evidence + (ref,))
    with pytest.raises(ValueError):
        dataclasses.replace(response, evidence=(dataclasses.replace(ref, observation_id=ObservationId("other")),))


@pytest.mark.parametrize(
    "field,value", [("content_sha256", "x" * 64), ("prepared_digest", "0"), ("schema", VersionRef("other", "1"))]
)
def test_invalid_reference_identity_is_refused(field, value):
    fields = dict(
        observation_id=ObservationId("o"),
        schema=VersionRef("autonomous_mixing.p1_directional_evidence", "3"),
        content_sha256="a" * 64,
        prepared_digest="b" * 64,
    )
    fields[field] = value
    with pytest.raises(ValueError):
        AnalysisEvidenceReference(**fields)


def test_references_cannot_be_rebound_to_other_current_sources(tmp_path):
    world = experimental_world(tmp_path)
    response = measure(world).value
    # Reverse the valid ordered pair in the observation but keep the original evidence file.
    wrong = dataclasses.replace(
        response,
        observations=tuple(
            dataclasses.replace(o, artifact_ids=tuple(reversed(o.artifact_ids))) for o in response.observations
        ),
    )
    with pytest.raises(ValueError, match="evidence"):
        world.make().verify_evidence(wrong)


def test_legacy_empty_response_has_no_fabricated_evidence():
    assert AnalysisResponse(()).evidence == ()


def test_router_preserves_all_evidence_associations(tmp_path):
    from autonomous_mixing.adapters.dsp_analysis.analysis_router import AnalysisRouter
    from autonomous_mixing.adapters.dsp_analysis.directional_analysis import ANALYZER

    world = experimental_world(tmp_path)
    result = measure(world, analyzer=AnalysisRouter({ANALYZER: world.analyzer}))
    assert isinstance(result, Success)
    assert len(result.value.evidence) == len(result.value.observations)
    world.make().verify_evidence(result.value)


@pytest.mark.parametrize("missing", ["manifest", "evidence"])
def test_missing_persisted_files_never_trigger_dsp_reconstruction(tmp_path, monkeypatch, missing):
    import autonomous_mixing.adapters.dsp_analysis.directional_analysis as module

    world = experimental_world(tmp_path)
    response = measure(world).value
    if missing == "manifest":
        from autonomous_mixing.adapters.dsp_analysis.masking_preparation import manifest_path

        manifest_path(world.plans_dir, world.plan.prepared_digest).unlink()
    else:
        (world.evidence_dir / (response.evidence[0].content_sha256 + ".json")).unlink()
    monkeypatch.setattr(module, "dist10_clip_to_bands", lambda *a, **kw: pytest.fail("must not reanalyze"))
    with pytest.raises(ValueError, match="evidence"):
        world.make().verify_evidence(response)


@pytest.mark.parametrize(
    "field",
    ["geometry", "policy_digest", "policy", "support_digest", "cells", "noise_reference_sha256", "baseline_sources"],
)
def test_content_hash_does_not_replace_manifest_identity_validation(tmp_path, field):
    import hashlib

    world = experimental_world(tmp_path)
    response = measure(world).value
    ref = response.evidence[0]
    doc = json.loads((world.evidence_dir / (ref.content_sha256 + ".json")).read_bytes())
    if field == "geometry":
        doc["support"]["digest"] = "0" * 64
    elif field == "cells":
        doc["experimental"][field] = doc["experimental"][field][1:]
    elif field == "policy":
        doc["experimental"][field]["energy_above_noise_db"] = 0.0
    elif field == "baseline_sources":
        doc["experimental"][field]["masker_sha256"] = "0" * 64
    else:
        doc["experimental"][field] = "0" * 64
    payload = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    digest = hashlib.sha256(payload).hexdigest()
    (world.evidence_dir / (digest + ".json")).write_bytes(payload)
    changed = dataclasses.replace(
        response, evidence=tuple(dataclasses.replace(r, content_sha256=digest) for r in response.evidence)
    )
    with pytest.raises(ValueError, match="evidence"):
        world.make().verify_evidence(changed)


# --- continuation must verify recorded P1 evidence without regenerating it (plan 4.1/4.3; 2026-11-23) -------------


def _trace_with_recorded_p1_analysis(tmp_path):
    """A minimal recorded trace: the frozen provider-bound RunRequest, then one ANALYZE intent/observation pair."""
    from types import SimpleNamespace

    from test_r5a_coordinator_gate import bound_harness

    from autonomous_mixing.core.records.acceptance import AcceptedReference
    from autonomous_mixing.core.records.identifiers import RunEventId
    from autonomous_mixing.core.records.run_trace import DispatchIntent, OperationKind, OperationObserved

    (tmp_path / "world").mkdir()
    world = experimental_world(tmp_path / "world")
    h, audit = bound_harness(tmp_path)
    from autonomous_mixing.core.records.masking_diagnosis import (
        EXPERIMENTAL_DIAGNOSIS_METRICS,
        ExperimentalMaskingDiagnosisJob,
        ExperimentalMaskingDiagnosisPolicy,
        experimental_diagnosis_method,
    )

    jobs = tuple(
        ExperimentalMaskingDiagnosisJob(
            fixed,
            tuple(
                world.job(
                    metric,
                    fixed.geometry.pair.masker,
                    fixed.geometry.pair.target,
                    time_range=fixed.geometry.time_range,
                    unit=unit,
                )
                for metric, unit in EXPERIMENTAL_DIAGNOSIS_METRICS
            ),
        )
        for fixed in world.plan.prepared.experimental_supports
    )
    policy = ExperimentalMaskingDiagnosisPolicy(experimental_diagnosis_method(world.plan), world.plan, jobs)
    h.request = dataclasses.replace(
        h.request, provider_binding=dataclasses.replace(h.request.provider_binding, diagnosis_policy=policy)
    )
    request = _experimental_request(world)
    analysis = world.analyzer.analyze(request)
    assert isinstance(analysis, Success)
    intent = DispatchIntent(OperationKind.ANALYZE, request, AcceptedReference(world.stems.state, 0), None, None,
                            None)  # fmt: skip
    events = (
        SimpleNamespace(event_id=RunEventId("request"), payload=h.request),
        SimpleNamespace(event_id=RunEventId("analyze"), payload=intent),
        SimpleNamespace(event_id=RunEventId("analyzed"),
                        payload=OperationObserved(RunEventId("analyze"), OperationKind.ANALYZE, analysis.value)),
    )  # fmt: skip
    return world, h, audit, SimpleNamespace(events=events), analysis.value


def _experimental_request(world):
    """The same request ``measure`` builds: every experimental metric, forward direction, prepared interval."""
    from masking_support import TA, TB

    from autonomous_mixing.adapters.dsp_analysis.directional_analysis import EXPERIMENTAL_METRICS
    from autonomous_mixing.core.records.analysis import AnalysisRequest
    from autonomous_mixing.core.records.observation import AnalysisScope, AnalysisSpec

    interval = world.plan.prepared.supports[0].time_range
    metrics = tuple(EXPERIMENTAL_METRICS)
    definitions = tuple(world.job(m, TA, TB, time_range=interval, unit=EXPERIMENTAL_METRICS[m]) for m in metrics)
    return AnalysisRequest(world.stems.state, AnalysisScope((TA, TB), interval), AnalysisSpec(world.spec, metrics),
                           definitions, world.stems.bindings)  # fmt: skip


def test_continuation_verifies_recorded_p1_evidence_and_never_regenerates_it(tmp_path, monkeypatch):
    from test_claude_config import config

    import autonomous_mixing.adapters.dsp_analysis.directional_analysis as module
    from autonomous_mixing.drivers.decision_evidence import ConfiguredDecisionEvidenceSource

    world, h, audit, snapshot, response = _trace_with_recorded_p1_analysis(tmp_path)
    try:
        binding = h.request.provider_binding
        verified = ConfiguredDecisionEvidenceSource(binding=binding, config=config(), audit=audit,
                                                    analysis_verifier=world.make())  # fmt: skip
        assert verified.verify_run(snapshot) is None
        unverified = ConfiguredDecisionEvidenceSource(binding=binding, config=config(), audit=audit)
        assert "verifier" in (unverified.verify_run(snapshot) or "")
        (world.evidence_dir / (response.evidence[0].content_sha256 + ".json")).unlink()
        monkeypatch.setattr(module, "dist10_clip_to_bands", lambda *a, **kw: pytest.fail("must not reanalyze"))
        assert "evidence" in (verified.verify_run(snapshot) or "")
    finally:
        audit.close()


def test_every_returned_p1_observation_needs_its_evidence_reference(tmp_path):
    world = experimental_world(tmp_path)
    response = measure(world).value
    partial = dataclasses.replace(response, evidence=response.evidence[1:])  # structurally valid, one missing
    with pytest.raises(ValueError, match="evidence"):
        world.make().verify_evidence(partial)


def test_recorded_evidence_must_match_the_current_state_source_hashes(tmp_path):
    from masking_support import state_of

    world = experimental_world(tmp_path)
    response = measure(world).value
    world.make().verify_evidence(response, state=world.stems.state)
    first = world.stems.state.audio.artifacts[0]
    rehashed = dataclasses.replace(first, content_hash="0" * 64)  # same artifact ID, different bytes
    changed = state_of((rehashed, *world.stems.state.audio.artifacts[1:]))
    with pytest.raises(ValueError, match="evidence"):
        world.make().verify_evidence(response, state=changed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("analysis_transform", None),
        ("analysis_sha256", None),
        ("analysis_transform", "other@1"),
        ("analysis_sha256", "not-a-sha256"),
    ],
)
def test_schema_four_requires_valid_analysis_provenance(tmp_path, field, value):
    import hashlib

    from masking_support import TA, TB, World, fixture_counts

    from autonomous_mixing.adapters.dsp_analysis.directional_analysis import RAW_SIGNED_MARGIN_MEDIAN_DB
    from autonomous_mixing.core.records.masking_measurement import P1_FLOAT64_PROFILE

    world = World(
        tmp_path,
        {TA: fixture_counts("masker_1k_a8192"), TB: fixture_counts("target_2k_a1024")},
        profile=P1_FLOAT64_PROFILE,
    )
    result = world.analyzer.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)]))
    assert isinstance(result, Success)
    response = result.value
    ref = response.evidence[0]
    document = json.loads((world.evidence_dir / (ref.content_sha256 + ".json")).read_bytes())
    for source in document["sources"].values():
        if value is None:
            source.pop(field)
        else:
            source[field] = value
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    digest = hashlib.sha256(payload).hexdigest()
    (world.evidence_dir / (digest + ".json")).write_bytes(payload)
    changed = dataclasses.replace(
        response, evidence=tuple(dataclasses.replace(r, content_sha256=digest) for r in response.evidence)
    )
    with pytest.raises(ValueError, match="evidence"):
        world.make().verify_evidence(changed, state=world.stems.state)


def test_continuation_rejects_p1_plan_drift_before_delegating_file_verification(tmp_path):
    from types import SimpleNamespace

    from masking_diagnosis_support import experimental_policy
    from test_claude_config import config

    from autonomous_mixing.drivers.decision_evidence import ConfiguredDecisionEvidenceSource

    world, h, audit, snapshot, response = _trace_with_recorded_p1_analysis(tmp_path)
    try:
        wrong = dataclasses.replace(h.request.provider_binding, diagnosis_policy=experimental_policy())
        assert wrong.diagnosis_policy.measurement_plan.prepared_digest != response.evidence[0].prepared_digest
        snapshot.events[0].payload = dataclasses.replace(h.request, provider_binding=wrong)
        calls = []
        verifier = SimpleNamespace(verify_evidence=lambda *a, **kw: calls.append(a))
        source = ConfiguredDecisionEvidenceSource(
            binding=wrong, config=config(), audit=audit, analysis_verifier=verifier
        )
        assert "plan" in (source.verify_run(snapshot) or "")
        assert calls == []
    finally:
        audit.close()
