"""Focused tests for the additive domain foundation (project, context and observation records).

Values used here are arbitrary structural fixtures, not musical or analysis parameters.
"""

import ast
import dataclasses
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autonomous_mixing.domain.context import AssertionSource, ContextPreference, MusicalContext, TrackRole
from autonomous_mixing.domain.observation import (
    AnalysisSpec,
    MeasuredScalar,
    Observation,
    RenderSpec,
    Unavailable,
    validate_observation_sources,
)
from autonomous_mixing.domain.project import (
    AnalysisScope,
    ArtifactCheckpoint,
    ArtifactKind,
    AudioArtifact,
    FrequencyRange,
    ProjectSnapshot,
    TimeRange,
    Track,
)
from autonomous_mixing.domain.shared import (
    ArtifactId,
    CheckpointId,
    ContextId,
    ObservationId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)

UTC_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
HASH = "a" * 64
DOMAIN_DIR = Path(__file__).resolve().parents[2] / "src" / "autonomous_mixing" / "domain"
INVALID_NUMBERS = [True, False, math.nan, math.inf, -math.inf, "1.0", None]


def _tracks(*names: str) -> tuple[Track, ...]:
    return tuple(Track(TrackId(f"t{i}"), name) for i, name in enumerate(names, start=1))


_DEFAULT = object()


def _snapshot(tracks=_DEFAULT) -> ProjectSnapshot:
    return ProjectSnapshot(
        ProjectId("p1"), Revision("r1"), UTC_NOW, _tracks("A", "B") if tracks is _DEFAULT else tracks
    )


def _artifact(
    artifact_id: str = "a1",
    track_ids: tuple[str, ...] = ("t1",),
    *,
    project_id: str = "p1",
    revision: str = "r1",
    kind: ArtifactKind = ArtifactKind.STEM,
    start: float = 0.0,
    sample_rate: int = 48000,
    channels: int = 2,
    frames: int = 48000 * 10,
) -> AudioArtifact:
    return AudioArtifact(
        artifact_id=ArtifactId(artifact_id),
        project_id=ProjectId(project_id),
        revision=Revision(revision),
        source_track_ids=tuple(TrackId(t) for t in track_ids),
        kind=kind,
        location=f"renders/{artifact_id}.wav",
        timeline_start_seconds=start,
        sample_rate_hz=sample_rate,
        channel_count=channels,
        frame_count=frames,
        content_hash=HASH,
        render_spec=VersionRef("render", "1"),
    )


def _scope(*track_ids: str, start: float = 1.0, end: float = 2.0, freq: FrequencyRange | None = None) -> AnalysisScope:
    return AnalysisScope(tuple(TrackId(t) for t in track_ids), TimeRange(start, end), freq)


def _observation(scope: AnalysisScope, artifact_ids: tuple[str, ...] = ("a1",), result=_DEFAULT) -> Observation:
    return Observation(
        observation_id=ObservationId("o1"),
        metric=VersionRef("metric", "1"),
        artifact_ids=tuple(ArtifactId(a) for a in artifact_ids),
        scope=scope,
        result=MeasuredScalar(-12.5, "dBFS") if result is _DEFAULT else result,
        analysis_spec=VersionRef("spec", "1"),
        analyzer=VersionRef("analyzer", "0.1"),
        created_at=UTC_NOW,
    )


def _source() -> AssertionSource:
    return AssertionSource("producer", UTC_NOW)


# --- values -----------------------------------------------------------------


@pytest.mark.parametrize("cls", [ProjectId, TrackId, ArtifactId, ObservationId, ContextId, Revision])
@pytest.mark.parametrize("raw", ["", "   ", 7, None])
def test_identifiers_reject_empty_and_non_string(cls, raw):
    with pytest.raises(ValueError):
        cls(raw)


def test_identifiers_of_different_kinds_are_not_equal():
    assert TrackId("x") == TrackId("x")
    assert TrackId("x") != ArtifactId("x")


@pytest.mark.parametrize("name, version", [("", "1"), ("spec", ""), ("spec", None)])
def test_version_ref_requires_name_and_version(name, version):
    with pytest.raises(ValueError):
        VersionRef(name, version)


@pytest.mark.parametrize("value", INVALID_NUMBERS)
def test_rejects_nonfinite_boolean_and_string_numbers(value):
    with pytest.raises(ValueError):
        MeasuredScalar(value, "dB")
    with pytest.raises(ValueError):
        TimeRange(value, 10.0)
    with pytest.raises(ValueError):
        FrequencyRange(20.0, value)


def test_measured_scalar_accepts_int_and_float_and_requires_unit():
    assert MeasuredScalar(0, "dB").value == 0
    assert MeasuredScalar(-3.5, "dB").unit == "dB"
    with pytest.raises(ValueError):
        MeasuredScalar(1.0, "")


@pytest.mark.parametrize("value", [10**400, -(10**400)], ids=["positive", "negative"])
def test_integers_too_large_for_float_raise_value_error_not_overflow(value):
    # Regression: math.isfinite raised OverflowError for ints beyond the float range.
    with pytest.raises(ValueError, match="finite"):
        MeasuredScalar(value, "dB")
    with pytest.raises(ValueError, match="finite"):
        TimeRange(0.0, value)


def test_large_float_representable_integers_are_kept_unchanged():
    assert MeasuredScalar(2**60, "count").value == 2**60
    assert type(MeasuredScalar(2**60, "count").value) is int


@pytest.mark.parametrize(
    "timestamp",
    [datetime(2026, 10, 19, 12, 0), datetime(2026, 10, 19, 12, 0, tzinfo=timezone(timedelta(hours=1))), "2026-10-19"],
)
def test_timestamps_must_be_utc(timestamp):
    with pytest.raises(ValueError):
        AssertionSource("producer", timestamp)
    with pytest.raises(ValueError):
        ProjectSnapshot(ProjectId("p1"), Revision("r1"), timestamp, _tracks("A"))
    with pytest.raises(ValueError):
        dataclasses.replace(_observation(_scope("t1")), created_at=timestamp)


# --- project ------------------------------------------------------------------


@pytest.mark.parametrize("start, end", [(-0.1, 1.0), (1.0, 1.0), (2.0, 1.0)])
def test_time_range_rejects_negative_empty_and_reversed(start, end):
    with pytest.raises(ValueError):
        TimeRange(start, end)


@pytest.mark.parametrize("low, high", [(-1.0, 100.0), (100.0, 100.0), (200.0, 100.0)])
def test_frequency_range_rejects_negative_empty_and_reversed(low, high):
    with pytest.raises(ValueError):
        FrequencyRange(low, high)


def test_track_identity_is_independent_of_display_name():
    first, second = Track(TrackId("t1"), "Same name"), Track(TrackId("t2"), "Same name")
    assert first != second
    with pytest.raises(ValueError):
        Track(TrackId("t3"), " ")


def test_snapshot_requires_at_least_one_track_and_unique_ids():
    with pytest.raises(ValueError):
        _snapshot(tracks=())
    with pytest.raises(ValueError):
        ProjectSnapshot(
            ProjectId("p1"), Revision("r1"), UTC_NOW, (Track(TrackId("t1"), "A"), Track(TrackId("t1"), "B"))
        )


def test_snapshot_collections_are_immutable():
    tracks = [Track(TrackId("t1"), "A")]
    with pytest.raises(ValueError):
        ProjectSnapshot(ProjectId("p1"), Revision("r1"), UTC_NOW, tracks)
    snapshot = _snapshot()
    assert isinstance(snapshot.tracks, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.tracks = ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.tracks[0].name = "renamed"


def test_scope_requires_distinct_nonempty_tracks():
    with pytest.raises(ValueError):
        AnalysisScope((), TimeRange(0.0, 1.0))
    with pytest.raises(ValueError):
        AnalysisScope((TrackId("t1"), TrackId("t1")), TimeRange(0.0, 1.0))
    with pytest.raises(ValueError):
        AnalysisScope([TrackId("t1")], TimeRange(0.0, 1.0))


def test_scope_frequency_range_is_optional():
    assert _scope("t1").frequency_range is None
    assert _scope("t1", freq=FrequencyRange(0.0, 500.0)).frequency_range == FrequencyRange(0.0, 500.0)


def test_scope_rejects_unknown_track():
    snapshot = _snapshot()
    _scope("t1", "t2").validate_against(snapshot)
    with pytest.raises(ValueError, match="t9"):
        _scope("t1", "t9").validate_against(snapshot)


@pytest.mark.parametrize("channels", [1, 2])
def test_artifact_accepts_mono_and_stereo_metadata(channels):
    assert _artifact(channels=channels).channel_count == channels


def test_artifact_accepts_zero_frames_as_metadata():
    artifact = _artifact(frames=0)
    assert artifact.frame_count == 0
    assert artifact.timeline_end_seconds == artifact.timeline_start_seconds


@pytest.mark.parametrize(
    "overrides",
    [
        {"channels": 0},
        {"channels": 3},
        {"channels": True},
        {"sample_rate": 0},
        {"sample_rate": 44100.0},
        {"sample_rate": True},
        {"frames": -1},
        {"frames": 1.5},
        {"start": -0.5},
        {"start": math.nan},
        {"track_ids": ()},
        {"track_ids": ("t1", "t1")},
        {"kind": "stem"},
    ],
)
def test_artifact_rejects_invalid_metadata(overrides):
    with pytest.raises(ValueError):
        _artifact(**overrides)


@pytest.mark.parametrize("digest", ["", "a" * 63, "A" * 64, "g" * 64, "a" * 65])
def test_artifact_rejects_malformed_sha256_digest(digest):
    with pytest.raises(ValueError):
        dataclasses.replace(_artifact(), content_hash=digest)


# --- context ------------------------------------------------------------------


def test_roles_are_explicit_and_unknown_is_absence():
    snapshot = _snapshot(_tracks("Kick", "Bass"))
    context = MusicalContext(
        ContextId("c1"), "1", (TrackRole(TrackId("t2"), "foundation", _source()),), (), frozenset()
    )
    context.validate_against(snapshot)
    assert context.role_for(TrackId("t2")) == "foundation"
    assert context.role_for(TrackId("t1")) is None


def test_unknown_role_is_not_inferred_from_name():
    snapshot = _snapshot(_tracks("Kick", "Bass"))
    context = MusicalContext(ContextId("c1"), "1", (), (), frozenset())
    context.validate_against(snapshot)
    assert all(context.role_for(track.track_id) is None for track in snapshot.tracks)
    assert not hasattr(snapshot.tracks[0], "role")


def test_role_requires_nonempty_role_and_explicit_source():
    with pytest.raises(ValueError):
        TrackRole(TrackId("t1"), "", _source())
    with pytest.raises(ValueError):
        TrackRole(TrackId("t1"), "lead", None)


def test_context_rejects_duplicate_role_assignment_and_mutable_collections():
    roles = (TrackRole(TrackId("t1"), "lead", _source()), TrackRole(TrackId("t1"), "texture", _source()))
    with pytest.raises(ValueError):
        MusicalContext(ContextId("c1"), "1", roles, (), frozenset())
    with pytest.raises(ValueError):
        MusicalContext(ContextId("c1"), "1", (), (), {TrackId("t1")})
    with pytest.raises(ValueError):
        MusicalContext(ContextId("c1"), "", (), (), frozenset())


@pytest.mark.parametrize(
    "roles, preferences, protected",
    [
        ((TrackRole(TrackId("t9"), "lead", _source()),), (), frozenset()),
        ((), (ContextPreference("keep it airy", (TrackId("t9"),), _source()),), frozenset()),
        ((), (), frozenset({TrackId("t9")})),
    ],
)
def test_context_rejects_unknown_referenced_tracks(roles, preferences, protected):
    context = MusicalContext(ContextId("c1"), "1", roles, preferences, protected)
    with pytest.raises(ValueError, match="t9"):
        context.validate_against(_snapshot())


def test_preference_may_be_project_wide_but_requires_description():
    assert ContextPreference("preserve dynamics", (), _source()).track_ids == ()
    with pytest.raises(ValueError):
        ContextPreference("", (), _source())
    with pytest.raises(ValueError):
        ContextPreference("x", (TrackId("t1"), TrackId("t1")), _source())


# --- observation --------------------------------------------------------------


def test_unavailable_is_explicit_and_not_a_zero_measurement():
    result = Unavailable("artifact has zero frames")
    assert not isinstance(result, MeasuredScalar)
    with pytest.raises(ValueError):
        Unavailable("")
    for silent_substitute in (0.0, None, math.nan):
        with pytest.raises(ValueError):
            _observation(_scope("t1"), result=silent_substitute)


def test_observation_requires_distinct_sources_and_typed_versions():
    with pytest.raises(ValueError):
        _observation(_scope("t1"), artifact_ids=())
    with pytest.raises(ValueError):
        _observation(_scope("t1"), artifact_ids=("a1", "a1"))
    with pytest.raises(ValueError):
        dataclasses.replace(_observation(_scope("t1")), metric="metric@1")


def test_analysis_and_render_specs_validate_shape():
    metric = VersionRef("metric", "1")
    assert AnalysisSpec(VersionRef("spec", "1"), (metric,)).metric_ids == (metric,)
    with pytest.raises(ValueError):
        AnalysisSpec(VersionRef("spec", "1"), ())
    with pytest.raises(ValueError):
        AnalysisSpec(VersionRef("spec", "1"), (metric, metric))
    assert RenderSpec(VersionRef("render", "1"), 48000, 1).channel_count == 1
    for rate, channels in [(0, 2), (48000, 3), (48000.0, 2), (True, 2)]:
        with pytest.raises(ValueError):
            RenderSpec(VersionRef("render", "1"), rate, channels)


def test_observations_over_three_arbitrary_declared_roles():
    snapshot = _snapshot(_tracks("Field recording", "Choir", "Tape loop"))
    context = MusicalContext(
        ContextId("c1"),
        "1",
        tuple(
            TrackRole(TrackId(t), role, _source())
            for t, role in [("t1", "ambience"), ("t2", "lead"), ("t3", "texture")]
        ),
        (),
        frozenset({TrackId("t2")}),
    )
    context.validate_against(snapshot)
    artifacts = (_artifact("a1", ("t1",)), _artifact("a2", ("t2", "t3"), kind=ArtifactKind.GROUP))
    observation = _observation(_scope("t1", "t2", "t3"), artifact_ids=("a1", "a2"), result=Unavailable("not analyzed"))
    validate_observation_sources(observation, artifacts, _checkpoint(*artifacts), snapshot)


# --- cross-record integrity ----------------------------------------------------


def _checkpoint(*artifacts: AudioArtifact, revision: str = "r1", project: str = "p1") -> ArtifactCheckpoint:
    return ArtifactCheckpoint(CheckpointId("cp"), ProjectId(project), Revision(revision), artifacts)


def _snapshot_at(revision: str) -> ProjectSnapshot:
    return ProjectSnapshot(ProjectId("p1"), Revision(revision), UTC_NOW, _tracks("A", "B"))


def test_sources_accept_matching_artifacts_and_ignore_unreferenced_ones():
    artifacts = (_artifact("a1", ("t1",)), _artifact("a2", ("t1", "t2"), kind=ArtifactKind.MIX))
    validate_observation_sources(_observation(_scope("t1")), artifacts, _checkpoint(*artifacts), _snapshot())


def test_sources_accept_mixed_production_revisions_within_checkpoint_membership():
    # State r2 retains an artifact produced at r1 next to one produced at r2; neither is retagged.
    retained, produced = _artifact("a1", ("t1",), revision="r1"), _artifact("c2", ("t2",), revision="r2")
    observation = _observation(_scope("t1", "t2"), artifact_ids=("a1", "c2"))
    checkpoint = _checkpoint(retained, produced, revision="r2")
    validate_observation_sources(observation, (retained, produced), checkpoint, _snapshot_at("r2"))
    assert retained.revision == Revision("r1")


@pytest.mark.parametrize(
    "artifacts, scope, reason",
    [
        ((_artifact(track_ids=("t1",)),), _scope("t1", "t2"), "not covered"),
        ((_artifact(start=1.5),), _scope("t1"), "timeline"),
        ((_artifact(frames=48000 * 2 - 1),), _scope("t1"), "timeline"),
        ((_artifact(frames=0),), _scope("t1"), "timeline"),
        ((_artifact(sample_rate=8000),), _scope("t1", freq=FrequencyRange(0.0, 4001.0)), "Nyquist"),
    ],
)
def test_sources_reject_invalid_metadata_coverage(artifacts, scope, reason):
    with pytest.raises(ValueError, match=reason):
        validate_observation_sources(_observation(scope), artifacts, _checkpoint(*artifacts), _snapshot())


def test_sources_reject_missing_and_duplicate_supplied_artifacts():
    member = _artifact("a1")
    with pytest.raises(ValueError, match="missing"):
        validate_observation_sources(_observation(_scope("t1")), (), _checkpoint(member), _snapshot())
    with pytest.raises(ValueError, match="duplicate"):
        validate_observation_sources(_observation(_scope("t1")), (member, member), _checkpoint(member), _snapshot())


def test_sources_reject_foreign_artifact_not_in_checkpoint():
    foreign, member = _artifact("a1"), _artifact("a2")
    with pytest.raises(ValueError, match="not a member"):
        validate_observation_sources(_observation(_scope("t1")), (foreign,), _checkpoint(member), _snapshot())


@pytest.mark.parametrize(
    "field, value",
    [("content_hash", "c" * 64), ("location", "elsewhere.wav"), ("revision", Revision("r0")), ("frame_count", 1)],
)
def test_sources_reject_forged_metadata_with_a_member_id(field, value):
    recorded = _artifact("a1")
    forged = dataclasses.replace(recorded, **{field: value})
    with pytest.raises(ValueError, match="does not match"):
        validate_observation_sources(_observation(_scope("t1")), (forged,), _checkpoint(recorded), _snapshot())


@pytest.mark.parametrize(
    "checkpoint, reason",
    [
        (lambda: _checkpoint(_artifact(), revision="r2"), "revision"),
        (lambda: _checkpoint(_artifact(project_id="p2"), project="p2"), "project"),
    ],
)
def test_sources_require_checkpoint_state_matching_snapshot(checkpoint, reason):
    cp = checkpoint()
    with pytest.raises(ValueError, match=reason):
        validate_observation_sources(_observation(_scope("t1")), cp.artifacts, cp, _snapshot())


def test_sources_reject_unknown_tracks():
    member = _artifact(track_ids=("t1", "t9"))
    with pytest.raises(ValueError, match="t9"):
        validate_observation_sources(_observation(_scope("t1")), (member,), _checkpoint(member), _snapshot())
    with pytest.raises(ValueError, match="t9"):
        validate_observation_sources(_observation(_scope("t9")), (member,), _checkpoint(member), _snapshot())


def test_sources_accept_exact_coverage_and_nyquist_boundary():
    artifact = _artifact(start=1.0, sample_rate=8000, frames=8000)
    scope = _scope("t1", start=1.0, end=2.0, freq=FrequencyRange(0.0, 4000.0))
    validate_observation_sources(_observation(scope), (artifact,), _checkpoint(artifact), _snapshot())


# --- derived-value overflow ----------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"frames": 10**400},
        {"sample_rate": 10**400, "frames": 0},
        {"start": 1.5e308, "frames": 48000 * 10**308},
    ],
    ids=["frame-count", "sample-rate", "end-overflows-to-infinity"],
)
def test_artifact_rejects_metadata_whose_derived_values_overflow(overrides):
    with pytest.raises(ValueError, match="finite"):
        _artifact(**overrides)


def test_render_spec_rejects_sample_rate_whose_nyquist_overflows():
    with pytest.raises(ValueError, match="finite"):
        RenderSpec(VersionRef("render", "1"), 10**400, 2)
    assert RenderSpec(VersionRef("render", "1"), 48000, 2).nyquist_hz == 24000


def test_valid_artifact_derived_values_still_work():
    artifact = _artifact(start=2.0, sample_rate=48000, frames=96000)
    assert artifact.timeline_end_seconds == 4.0
    assert artifact.nyquist_hz == 24000


# --- dependency boundary -------------------------------------------------------

DOMAIN_PACKAGE = "autonomous_mixing.domain"


RECORDS_PACKAGE = "autonomous_mixing.core.records"


def _boundary_violations(source: str, module_name: str, *, is_package: bool = False) -> list[str]:
    """Return imports in ``source`` that are neither standard library nor inside the domain package.

    Relative imports are resolved against the package of ``module_name`` (the module itself when
    ``is_package``). For ``from X import name``, only ``X`` is checked, so ``from autonomous_mixing
    import domain`` is rejected conservatively.
    """
    package = module_name.split(".") if is_package else module_name.split(".")[:-1]
    violations = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            relative = False
        elif isinstance(node, ast.ImportFrom):
            relative = node.level > 0
            if relative:
                if node.level - 1 >= len(package):
                    violations.append("." * node.level + (node.module or ""))
                    continue  # escapes beyond the top-level package
                base = package[: len(package) - (node.level - 1)]
                names = [".".join(base + ([node.module] if node.module else []))]
            else:
                names = [node.module or ""]
        else:
            continue
        for name in names:
            in_domain = name == DOMAIN_PACKAGE or name.startswith(DOMAIN_PACKAGE + ".")
            # ADR-027 migration step M1: domain records moved to the core records package.
            in_domain = in_domain or name == RECORDS_PACKAGE or name.startswith(RECORDS_PACKAGE + ".")
            in_stdlib = not relative and name.split(".")[0] in sys.stdlib_module_names
            if not (in_domain or in_stdlib):
                violations.append(name)
    return violations


@pytest.mark.parametrize(
    "source",
    [
        "import math",
        "from collections.abc import Iterable",
        "from __future__ import annotations",
        "import autonomous_mixing.domain",
        "from autonomous_mixing.domain import values",
        "from autonomous_mixing.domain.values import TrackId",
        "from . import values",
        "from .values import TrackId",
        "from ..domain.project import Track",
    ],
)
def test_boundary_checker_allows_standard_library_and_domain_imports(source):
    assert _boundary_violations(source, f"{DOMAIN_PACKAGE}.observation") == []


@pytest.mark.parametrize(
    "source",
    [
        "import numpy",
        "import autonomous_mixing",
        "import autonomous_mixing.audio.io",
        "from autonomous_mixing import cli",
        "from autonomous_mixing.domain_extra import x",
        "import autonomous_mixing.domain_extra",
        "from ..audio.io import load_stem",
        "from .. import audio",
        "from ...elsewhere import x",
        "from .... import x",
    ],
)
def test_boundary_checker_rejects_imports_escaping_the_domain(source):
    assert _boundary_violations(source, f"{DOMAIN_PACKAGE}.observation") != []


def test_boundary_checker_resolves_relative_imports_from_package_init():
    init = DOMAIN_PACKAGE
    assert _boundary_violations("from .values import TrackId", init, is_package=True) == []
    assert _boundary_violations("from .audio import io", init, is_package=True) == []
    assert _boundary_violations("from ..audio import io", init, is_package=True) != []


def test_domain_modules_import_only_standard_library_and_domain():
    # Recursive: the domain is organized into component packages (ADR-017); caches are not scanned.
    modules = sorted(m for m in DOMAIN_DIR.rglob("*.py") if "__pycache__" not in m.parts)
    relative = {m.relative_to(DOMAIN_DIR).as_posix() for m in modules}
    assert relative >= {
        "__init__.py",
        "values.py",
        "project/__init__.py",
        "project/checkpoint.py",
        "context/musical_context.py",
        "observation/provenance.py",
        "action/execution.py",
        "run/attempt.py",
    }
    for module in modules:
        parts = (DOMAIN_PACKAGE.split(".")) + list(module.relative_to(DOMAIN_DIR).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        module_name = ".".join(parts[:-1] if is_package else parts)
        violations = _boundary_violations(module.read_text(encoding="utf-8"), module_name, is_package=is_package)
        assert violations == [], f"{module} imports {violations}"
