"""Immutable readiness configuration; all measurements here are labelled contract fixtures."""

from dataclasses import FrozenInstanceError, replace

import pytest
from masking_diagnosis_support import definitions, plan, policy, support
from masking_support import TA, TB

from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.masking_diagnosis import (
    READINESS_METRICS,
    MaskingDiagnosisJob,
    MaskingDiagnosisPolicy,
)
from autonomous_mixing.core.records.masking_measurement import BandRegion
from autonomous_mixing.core.records.observation import FrequencyRange


def test_required_metrics_match_the_real_r1_catalogue():
    from autonomous_mixing.adapters.dsp_analysis.directional_analysis import QUALIFIED_METRICS

    assert dict(READINESS_METRICS) == {
        k: v
        for k, v in QUALIFIED_METRICS.items()
        if k.name in ("p1.support_cells", "p1.robust_exceedance_cells", "p1.longest_robust_run_seconds")
    }
    assert len(READINESS_METRICS) == 3


def test_policy_is_frozen_and_has_no_qualification_switch_or_thresholds():
    p = policy()
    with pytest.raises(FrozenInstanceError):
        p.jobs = ()
    assert set(p.__dataclass_fields__) == {"method", "measurement_plan", "jobs"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: replace(d, unit="wrong"),
        lambda d: replace(d, selector=replace(d.selector, metric=VersionRef("p1.raw.geometric_support_cells", "1"))),
        lambda d: replace(d, selector=replace(d.selector, scope=replace(d.selector.scope, track_ids=(TB, TA)))),
        lambda d: replace(
            d, selector=replace(d.selector, scope=replace(d.selector.scope, frequency_range=FrequencyRange(10, 100)))
        ),
        lambda d: replace(d, alignment=VersionRef("other", "1")),
        lambda d: replace(d, sources=tuple(reversed(d.sources))),
        lambda d: replace(d, sources=(replace(d.sources[0], channel_count=2), d.sources[1])),
        lambda d: replace(d, sources=tuple(replace(s, sample_rate_hz=48000) for s in d.sources)),
        lambda d: replace(d, sources=tuple(replace(s, frame_count=5000) for s in d.sources)),
    ],
)
def test_job_rejects_invalid_exact_bindings(mutate):
    p = policy()
    j = p.jobs[0]
    with pytest.raises(ValueError):
        replace(j, measurements=(mutate(j.measurements[0]), *j.measurements[1:]))


@pytest.mark.parametrize("field", ["analysis_spec", "analyzer"])
def test_policy_rejects_other_plan_settings(field):
    p = policy()
    j = p.jobs[0]
    changed = tuple(replace(d, **{field: VersionRef("other", "1")}) for d in j.measurements)
    with pytest.raises(ValueError):
        replace(p, jobs=(replace(j, measurements=changed),))


@pytest.mark.parametrize("measurements", [(), [], None])
def test_missing_or_mutable_measurements_are_rejected(measurements):
    with pytest.raises(ValueError):
        MaskingDiagnosisJob(support(), measurements)


def test_duplicate_and_missing_metric_bindings_are_rejected():
    p = policy()
    j = p.jobs[0]
    for ds in (j.measurements[:2], (j.measurements[0],) * 3):
        with pytest.raises(ValueError):
            replace(j, measurements=ds)


@pytest.mark.parametrize(
    "other",
    [
        lambda: support(pair=(TB, TA)),
        lambda: support(region=BandRegion("renamed", tuple(range(1, 21)))),
        lambda: support(region=BandRegion("shifted", tuple(range(2, 21)))),
    ],
)
def test_policy_requires_full_support_membership(other):
    p = policy()
    s = other()
    with pytest.raises(ValueError):
        replace(p, jobs=(MaskingDiagnosisJob(s, definitions(s, p.measurement_plan)),))


def test_equal_cell_counts_with_different_cells_are_incompatible():
    a = support(region=BandRegion("a", (1, 2)))
    b = support(region=BandRegion("b", (2, 3)))
    assert a.cell_count == b.cell_count and a.digest != b.digest
    p = plan((a,))
    with pytest.raises(ValueError):
        MaskingDiagnosisPolicy(VersionRef("method", "1"), p, (MaskingDiagnosisJob(b, definitions(b, p)),))


@pytest.mark.parametrize("jobs", [(), [], None])
def test_policy_requires_immutable_nonempty_jobs(jobs):
    with pytest.raises(ValueError):
        MaskingDiagnosisPolicy(VersionRef("method", "1"), plan(), jobs)


def test_duplicate_job_rejected():
    p = policy()
    with pytest.raises(ValueError):
        replace(p, jobs=(p.jobs[0], p.jobs[0]))


@pytest.mark.parametrize("changes", [{"channel_count": 2}, {"frame_count": 5000}, {"timeline_start_seconds": 1.0}])
def test_consistent_but_wrong_source_metadata_is_rejected(changes):
    p = policy()
    j = p.jobs[0]
    measurements = tuple(replace(d, sources=tuple(replace(s, **changes) for s in d.sources)) for d in j.measurements)
    with pytest.raises(ValueError):
        candidate = replace(j, measurements=measurements)
        replace(p, jobs=(candidate,))  # Geometry is job-level; source-channel admission belongs to the v1 policy.


def test_same_support_digest_does_not_allow_changed_provenance():
    p = policy()
    j = p.jobs[0]
    changed = replace(j.support, masker_sha256="a" * 64)
    assert changed.digest == j.support.digest
    with pytest.raises(ValueError):
        replace(p, jobs=(MaskingDiagnosisJob(changed, j.measurements),))
