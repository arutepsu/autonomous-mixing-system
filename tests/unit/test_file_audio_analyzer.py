"""Tests for ``FileAudioAnalyzer`` (file-audio-analyzer design; level kernels in ``dsp_analysis.levels``).

Fixtures are tiny analytical float64 arrays written as WAV/DOUBLE files into ``tmp_path`` across two
roots; they are unit-test signals with closed-form levels, not musical material or evaluation data. Track
identities are generic. The sample rate of 8 Hz is chosen so that frame times are exact binary fractions;
non-grid times are tested on metadata-only artifacts. Tests marked "spy" wrap an owner-module function to
count calls; all reads are real. The coordinator test at the end uses the real analyzer, renderer and
integrity inspector with labelled controlled diagnosis and decisions and labelled test criteria: it shows
that measured levels respond to rendered gain changes and feed verification, not that a change is
musically correct.
"""

import dataclasses
import hashlib
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import autonomous_mixing.adapters.dsp_analysis.file_analyzer as analyzer_module
from autonomous_mixing.adapters.dsp_analysis.file_analyzer import (
    ANALYZER,
    SAMPLE_PEAK_DBFS,
    SAMPLE_RMS_DBFS,
    FileAudioAnalyzer,
    frame_bounds,
)
from autonomous_mixing.adapters.dsp_analysis.levels import EMPTY_TIME_SELECTION, SILENT_SIGNAL
from autonomous_mixing.core.ports.analysis import AudioAnalyzer
from autonomous_mixing.core.records.analysis import AnalysisRequest, AnalysisResponse
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ObservationId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    AnalysisSpec,
    FrequencyRange,
    MeasuredScalar,
    TimeRange,
    Unavailable,
)
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success
from autonomous_mixing.core.records.verification import MetricSelector
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition, SourceRequirement

RATE = 8
FRAMES = 8
UTC = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
RENDER_REF = VersionRef("render", "1")
SPEC = VersionRef("analysis-spec", "1")
ALIGNMENT = VersionRef("alignment", "1")
T1, T2 = TrackId("t1"), TrackId("t2")
P1 = ProjectId("p1")
LIMIT = 1 << 20
FULL = TimeRange(0.0, FRAMES / RATE)
# Labelled analytical samples (stereo, eight frames at 8 Hz): antiphase 0.5 for the first half second,
# in-phase 0.25 for the second; the second stem is exact digital silence.
S1_SAMPLES = np.array([[0.5, -0.5]] * 4 + [[0.25, 0.25]] * 4)
S2_SAMPLES = np.zeros((FRAMES, 2))
MIX_SAMPLES = S1_SAMPLES + S2_SAMPLES


def _db(value: float) -> float:
    return 20.0 * math.log10(value)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Clock:
    def __init__(self, reading=None):
        self.reads, self.reading = 0, reading

    def read(self):
        self.reads += 1
        if self.reading is not None:
            return self.reading
        return ClockReading(float(self.reads), UTC + timedelta(seconds=self.reads), False)


class Tokens:
    def __init__(self, tokens=None, prefix="obs"):
        self.tokens, self.prefix, self.count = list(tokens or ()), prefix, 0

    def next_token(self):
        self.count += 1
        return self.tokens.pop(0) if self.tokens else f"{self.prefix}-{self.count}"


class Files:
    """Two current stems in an input root and the current mix in an output root (native files)."""

    def __init__(self, tmp: Path):
        self.dirs = {key: tmp / key for key in ("inputs", "outputs")}
        for path in self.dirs.values():
            path.mkdir()
        self.roots = {key: str(path) for key, path in self.dirs.items()}
        self.s1 = self.write("inputs", "s1", (T1,), S1_SAMPLES)
        self.s2 = self.write("inputs", "s2", (T2,), S2_SAMPLES)
        self.mix = self.write("outputs", "mix", (T1, T2), MIX_SAMPLES, ArtifactKind.MIX)
        self.keys = {"s1": "inputs", "s2": "inputs", "mix": "outputs"}
        self.state = self.state_of((self.s1, self.s2, self.mix))

    def write(self, root, aid, tracks, samples, kind=ArtifactKind.STEM) -> AudioArtifact:
        path = self.dirs[root] / f"{aid}.wav"
        sf.write(str(path), np.asarray(samples, dtype=np.float64), RATE, format="WAV", subtype="DOUBLE")
        return AudioArtifact(ArtifactId(aid), P1, Revision("r1"), tracks, kind, f"{aid}.wav", 0.0, RATE, 2,
                             len(samples), _sha(path), RENDER_REF)  # fmt: skip

    def state_of(self, members) -> ProjectState:
        snapshot = ProjectSnapshot(P1, Revision("r1"), UTC, (Track(T1, "track 1"), Track(T2, "track 2")))
        processing = ProjectProcessingState(P1, Revision("r1"), (TrackGainState(T1, self.s1, 0.0),
                                                                 TrackGainState(T2, self.s2, 0.0)))  # fmt: skip
        return ProjectState(snapshot, processing, ArtifactCheckpoint(CheckpointId("cp-r1"), P1, Revision("r1"),
                                                                     tuple(members)))  # fmt: skip

    def hashes(self) -> dict[str, str]:
        return {str(p): _sha(p) for d in self.dirs.values() for p in d.iterdir()}

    def request(self, definitions, *, state=None, keys=None, scope=None) -> AnalysisRequest:
        state = state or self.state
        keys = keys or self.keys
        metrics = tuple(dict.fromkeys(d.selector.metric for d in definitions))
        bindings = tuple(ArtifactRootBinding(a.artifact_id, keys[str(a.artifact_id)]) for a in state.audio.artifacts)
        return AnalysisRequest(state, scope or AnalysisScope((T1, T2), FULL), AnalysisSpec(SPEC, metrics),
                               tuple(definitions), bindings)  # fmt: skip


def source_of(artifact: AudioArtifact, **overrides) -> SourceRequirement:
    fields = dict(kind=artifact.kind, source_track_ids=artifact.source_track_ids, render_spec=artifact.render_spec,
                  sample_rate_hz=artifact.sample_rate_hz, channel_count=artifact.channel_count,
                  timeline_start_seconds=artifact.timeline_start_seconds, frame_count=artifact.frame_count)  # fmt: skip
    fields.update(overrides)
    return SourceRequirement(**fields)


def job(metric, artifact, time_range=FULL, *, tracks=None, sources=None, **overrides) -> MeasurementDefinition:
    scope = AnalysisScope(tracks or artifact.source_track_ids, time_range, overrides.pop("frequency_range", None))
    fields = dict(selector=MetricSelector(metric, scope), unit="dBFS", analysis_spec=SPEC, analyzer=ANALYZER,
                  alignment=ALIGNMENT, sources=sources or (source_of(artifact),))  # fmt: skip
    fields.update(overrides)
    return MeasurementDefinition(**fields)


@pytest.fixture
def files(tmp_path) -> Files:
    return Files(tmp_path)


def make(roots, *, clock=None, identities=None, **limits) -> FileAudioAnalyzer:
    limits = {"max_file_bytes": LIMIT, "max_decoded_bytes": LIMIT, **limits}
    return FileAudioAnalyzer(roots, clock=clock or Clock(), identities=identities or Tokens(), **limits)


class Spy:
    def __init__(self, function):
        self.function, self.calls = function, []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.function(*args, **kwargs)


@pytest.fixture
def spies(monkeypatch):
    resolve = Spy(analyzer_module.resolve_artifact_location)
    check = Spy(analyzer_module.check_resolved_wav_integrity)
    monkeypatch.setattr(analyzer_module, "resolve_artifact_location", resolve)
    monkeypatch.setattr(analyzer_module, "check_resolved_wav_integrity", check)
    return resolve, check


def _values(response: AnalysisResponse) -> list:
    return [o.result.value if isinstance(o.result, MeasuredScalar) else o.result for o in response.observations]


# --- construction -----------------------------------------------------------------------------------


def test_the_analyzer_satisfies_the_port(files):
    analyzer: AudioAnalyzer = make(files.roots)
    assert callable(analyzer.analyze)


@pytest.mark.parametrize("roots", [None, [("inputs", "/x")], {"": "/x"}, {"inputs": ""}, {"inputs": 3}, {1: "/x"}])
def test_constructor_validates_roots(roots):
    with pytest.raises(ValueError):
        make(roots)


@pytest.mark.parametrize("limits", [{"max_file_bytes": 0}, {"max_decoded_bytes": True}, {"max_file_bytes": 1.0}])
def test_constructor_requires_positive_integer_limits(limits):
    with pytest.raises(ValueError):
        make({"inputs": "/x"}, **limits)


def test_constructor_requires_clock_and_identity_ports():
    with pytest.raises(ValueError, match="clock"):
        FileAudioAnalyzer({}, max_file_bytes=1, max_decoded_bytes=1, clock=object(), identities=Tokens())
    with pytest.raises(ValueError, match="identities"):
        FileAudioAnalyzer({}, max_file_bytes=1, max_decoded_bytes=1, clock=Clock(), identities=object())


def test_construction_copies_roots_and_touches_no_filesystem(files, spies):
    roots = {"inputs": files.roots["inputs"], "outputs": "/does/not/exist"}
    analyzer = make(roots)
    roots["outputs"] = files.roots["outputs"]  # later caller mutation is not seen
    assert spies[0].calls == [] and spies[1].calls == []
    result = analyzer.analyze(files.request([job(SAMPLE_PEAK_DBFS, files.mix)]))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNAVAILABLE


def test_a_wrong_request_type_raises_before_any_file_access(files, spies):
    with pytest.raises(ValueError):
        make(files.roots).analyze(object())
    assert spies[0].calls == []


# --- measurements -----------------------------------------------------------------------------------


def test_peak_and_rms_of_real_files_match_their_analytical_values(files):
    clock, before = Clock(), files.hashes()
    request = files.request([job(SAMPLE_PEAK_DBFS, files.s1), job(SAMPLE_RMS_DBFS, files.s1)])
    result = make(files.roots, clock=clock).analyze(request)
    assert isinstance(result, Success)
    peak, rms = result.value.observations
    assert peak.result.value == pytest.approx(_db(0.5), rel=1e-12) and peak.result.unit == "dBFS"
    # pooled mean square (4 * 2 * 0.25 + 4 * 2 * 0.0625) / 16; antiphase frames do not cancel
    assert rms.result.value == pytest.approx(10 * math.log10(0.15625), rel=1e-12) and rms.result.unit == "dBFS"
    for observation, definition in zip(result.value.observations, request.definitions, strict=True):
        assert observation.metric == definition.selector.metric and observation.scope == definition.selector.scope
        assert observation.artifact_ids == (files.s1.artifact_id,)
        assert (observation.analyzer, observation.analysis_spec) == (ANALYZER, SPEC)
        assert observation.created_at == UTC + timedelta(seconds=1)
    assert [o.observation_id for o in result.value.observations] == [ObservationId("obs-1"), ObservationId("obs-2")]
    assert clock.reads == 1
    assert files.hashes() == before  # sources are read only


def test_jobs_across_roots_scopes_and_time_windows_return_in_job_order(files):
    definitions = [
        job(SAMPLE_RMS_DBFS, files.mix, TimeRange(0.5, 1.0)),  # second half of the mix
        job(SAMPLE_PEAK_DBFS, files.s1),
        job(SAMPLE_RMS_DBFS, files.mix),  # the same metric at a second scope
        job(SAMPLE_RMS_DBFS, files.mix, TimeRange(0.0, 0.5)),  # antiphase half: no cancellation
    ]
    result = make(files.roots).analyze(files.request(definitions))
    assert _values(result.value) == [
        pytest.approx(_db(0.25), rel=1e-12),
        pytest.approx(_db(0.5), rel=1e-12),
        pytest.approx(10 * math.log10(0.15625), rel=1e-12),
        pytest.approx(_db(0.5), rel=1e-12),
    ]
    assert [o.artifact_ids for o in result.value.observations] == [
        (files.mix.artifact_id,), (files.s1.artifact_id,), (files.mix.artifact_id,), (files.mix.artifact_id,)]  # fmt: skip


def test_each_distinct_source_is_verified_once_in_first_occurrence_order(files, spies):  # spy
    definitions = [
        job(SAMPLE_RMS_DBFS, files.mix),
        job(SAMPLE_PEAK_DBFS, files.s1),
        job(SAMPLE_PEAK_DBFS, files.mix),
        job(SAMPLE_RMS_DBFS, files.s1, TimeRange(0.0, 0.5)),
    ]
    result = make(files.roots).analyze(files.request(definitions))
    assert isinstance(result, Success)
    checked = [args[0].artifact.artifact_id for args, _ in spies[1].calls]
    assert checked == [files.mix.artifact_id, files.s1.artifact_id]


def test_a_subset_scope_is_measured_on_the_stem_not_the_mix(files):
    definition = job(SAMPLE_PEAK_DBFS, files.s1, tracks=(T1,))
    result = make(files.roots).analyze(files.request([definition]))
    assert result.value.observations[0].artifact_ids == (files.s1.artifact_id,)


def test_exact_silence_is_unavailable_and_still_cites_its_verified_source(files):
    definitions = [job(SAMPLE_PEAK_DBFS, files.s2), job(SAMPLE_RMS_DBFS, files.s2)]
    result = make(files.roots).analyze(files.request(definitions))
    assert _values(result.value) == [Unavailable(SILENT_SIGNAL), Unavailable(SILENT_SIGNAL)]
    assert {o.artifact_ids for o in result.value.observations} == {(files.s2.artifact_id,)}


def test_an_interval_between_frame_times_is_an_empty_selection(files):
    # frames lie at multiples of 0.125 s; [0.01, 0.1) contains none
    result = make(files.roots).analyze(files.request([job(SAMPLE_PEAK_DBFS, files.s1, TimeRange(0.01, 0.1))]))
    assert _values(result.value) == [Unavailable(EMPTY_TIME_SELECTION)]


def _metadata(start: float, rate: int, frames: int) -> AudioArtifact:
    return AudioArtifact(ArtifactId("m"), P1, Revision("r1"), (T1,), ArtifactKind.STEM, "m.wav", start, rate, 1,
                         frames, "0" * 64, RENDER_REF)  # fmt: skip


@pytest.mark.parametrize(
    "start, rate, frames, time_range, expected",
    [
        (0.0, 8, 8, TimeRange(0.0, 1.0), (0, 8)),
        (0.0, 8, 8, TimeRange(0.125, 0.375), (1, 3)),  # start-inclusive, end-exclusive at exact frame times
        (0.0, 8, 8, TimeRange(0.1, 0.3), (1, 3)),  # non-grid bounds: frames at 0.125 and 0.25
        (0.0, 3, 9, TimeRange(1 / 3, 2 / 3), (1, 2)),  # binary-inexact frame times compared by the same expression
        (0.1, 3, 9, TimeRange(0.2, 0.5), (1, 2)),  # offset timeline: 0.1 + 1/3 is the only frame inside
        (0.0, 8, 8, TimeRange(0.01, 0.1), (1, 1)),  # empty
        (2.0, 8, 8, TimeRange(2.5, 3.0), (4, 8)),
    ],
)
def test_frame_bounds_follow_the_declared_timestamp_expression(start, rate, frames, time_range, expected):
    assert frame_bounds(_metadata(start, rate, frames), time_range) == expected


# --- batch rejection before any file access ---------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"analyzer": VersionRef("autonomous_mixing.file_level_analyzer", "2")},
        {"analyzer": VersionRef("other", "1")},
        {"unit": "dB"},
        {"frequency_range": FrequencyRange(0.0, 4.0)},  # even a band spanning 0 Hz to Nyquist is unsupported
    ],
)
def test_unsupported_job_shapes_fail_the_whole_batch_before_file_access(files, spies, overrides):
    definitions = [job(SAMPLE_PEAK_DBFS, files.s1), job(SAMPLE_RMS_DBFS, files.mix, **overrides)]
    result = make(files.roots).analyze(files.request(definitions))
    assert isinstance(result, PortFailure) and (result.code, result.retryable) == (PortFailureCode.UNSUPPORTED, False)
    assert spies[0].calls == [] and spies[1].calls == []


def test_an_unimplemented_metric_is_unsupported(files, spies):
    result = make(files.roots).analyze(files.request([job(VersionRef("audio.sample_peak_dbfs", "2"), files.s1)]))
    assert result.code is PortFailureCode.UNSUPPORTED and spies[1].calls == []


def test_a_source_pair_is_unsupported(files, spies):
    definition = job(SAMPLE_RMS_DBFS, files.s1, tracks=(T1, T2), sources=(source_of(files.s1), source_of(files.s2)))
    result = make(files.roots).analyze(files.request([definition]))
    assert result.code is PortFailureCode.UNSUPPORTED and "exactly one source" in result.message


def test_a_contributor_subset_of_a_combined_source_is_unsupported(files, spies):
    definition = job(SAMPLE_RMS_DBFS, files.mix, tracks=(T1,))  # the mix is not an isolated T1 measurement
    result = make(files.roots).analyze(files.request([definition]))
    assert result.code is PortFailureCode.UNSUPPORTED and spies[0].calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"frame_count": FRAMES + 1},
        {"sample_rate_hz": 16},
        {"render_spec": VersionRef("render", "2")},
        {"timeline_start_seconds": 0.5},
        {"kind": ArtifactKind.GROUP},
        {"source_track_ids": (T2, T1)},  # contributor order is part of the subject
    ],
)
def test_a_source_subject_without_a_current_match_is_invalid_input(files, spies, overrides):
    definition = job(SAMPLE_PEAK_DBFS, files.mix, sources=(source_of(files.mix, **overrides),))
    result = make(files.roots).analyze(files.request([definition]))
    assert isinstance(result, PortFailure) and (result.code, result.retryable) == (PortFailureCode.INVALID_INPUT, False)
    assert "no current artifacts match" in result.message and spies[0].calls == []


def test_an_ambiguous_source_subject_is_invalid_input_never_the_first_match(files, spies):
    twin = files.write("outputs", "mix-b", (T1, T2), MIX_SAMPLES, ArtifactKind.MIX)
    state = files.state_of((files.s1, files.s2, files.mix, twin))
    keys = {**files.keys, "mix-b": "outputs"}
    result = make(files.roots).analyze(files.request([job(SAMPLE_PEAK_DBFS, files.mix)], state=state, keys=keys))
    assert result.code is PortFailureCode.INVALID_INPUT and "2 ambiguous" in result.message
    assert spies[0].calls == []


def test_a_fixed_input_outside_the_current_checkpoint_is_not_an_analysis_source(files, spies):
    state = files.state_of((files.s2, files.mix))  # s1 is still the T1 gain input but no current member
    keys = {"s2": "inputs", "mix": "outputs"}
    result = make(files.roots).analyze(files.request([job(SAMPLE_PEAK_DBFS, files.s1)], state=state, keys=keys))
    assert result.code is PortFailureCode.INVALID_INPUT and spies[0].calls == []


def test_an_unconfigured_root_key_is_invalid_input(files, spies):
    result = make({"inputs": files.roots["inputs"]}).analyze(files.request([job(SAMPLE_PEAK_DBFS, files.mix)]))
    assert result.code is PortFailureCode.INVALID_INPUT and "'outputs'" in result.message and spies[0].calls == []


def test_a_source_that_does_not_cover_the_interval_is_invalid_input(files, spies):
    short = files.write("outputs", "short", (T1,), S1_SAMPLES[:4])  # covers [0, 0.5) only
    state = files.state_of((short, files.s2, files.mix))
    keys = {**files.keys, "short": "outputs"}
    request = files.request([job(SAMPLE_PEAK_DBFS, short, TimeRange(0.25, 0.75))], state=state, keys=keys)
    result = make(files.roots).analyze(request)
    assert result.code is PortFailureCode.INVALID_INPUT and "does not cover" in result.message
    assert spies[0].calls == []


def test_unsupported_is_reported_before_invalid_input_anywhere_in_the_batch(files):
    definitions = [
        job(SAMPLE_PEAK_DBFS, files.mix, sources=(source_of(files.mix, frame_count=1),)),
        job(SAMPLE_RMS_DBFS, files.s1, unit="dB"),
    ]
    assert make(files.roots).analyze(files.request(definitions)).code is PortFailureCode.UNSUPPORTED


# --- typed reader failures --------------------------------------------------------------------------


def _replace_file(files, root, aid, samples):
    sf.write(str(files.dirs[root] / f"{aid}.wav"), samples, RATE, format="WAV", subtype="DOUBLE")


@pytest.mark.parametrize(
    "damage, code",
    [
        (lambda f: _replace_file(f, "outputs", "mix", MIX_SAMPLES * 0.5), "hash_mismatch"),
        (lambda f: (f.dirs["outputs"] / "mix.wav").unlink(), "not_found"),
        (lambda f: (f.dirs["outputs"] / "mix.wav").write_bytes(b"not a wav"), "hash_mismatch"),
    ],
)
def test_reader_failures_return_unavailable_with_their_code_and_no_partial_batch(files, damage, code):
    damage(files)
    request = files.request([job(SAMPLE_PEAK_DBFS, files.s1), job(SAMPLE_PEAK_DBFS, files.mix)])
    result = make(files.roots).analyze(request)
    assert isinstance(result, PortFailure) and (result.code, result.retryable) == (PortFailureCode.UNAVAILABLE, False)
    assert result.message.startswith(f"{code}:") and "artifact mix" in result.message


def test_metadata_mismatch_with_a_matching_hash_is_unavailable(files):
    wrong = dataclasses.replace(files.mix, frame_count=FRAMES - 1)  # declared metadata disagrees with the file
    state = files.state_of((files.s1, files.s2, wrong))
    definition = job(SAMPLE_PEAK_DBFS, wrong, TimeRange(0.0, 0.5))
    result = make(files.roots).analyze(files.request([definition], state=state))
    assert result.code is PortFailureCode.UNAVAILABLE and result.message.startswith("metadata_mismatch:")


def test_non_wav_content_with_its_declared_hash_is_unavailable(files):
    path = files.dirs["outputs"] / "mix.wav"
    path.write_bytes(b"not a wav file")
    declared = dataclasses.replace(files.mix, content_hash=_sha(path))  # hash agrees, format does not
    state = files.state_of((files.s1, files.s2, declared))
    result = make(files.roots).analyze(files.request([job(SAMPLE_PEAK_DBFS, declared)], state=state))
    assert result.code is PortFailureCode.UNAVAILABLE and result.message.startswith("invalid_wav:")


def test_a_file_over_the_read_limit_is_unavailable(files):
    result = make(files.roots, max_file_bytes=16).analyze(files.request([job(SAMPLE_PEAK_DBFS, files.s1)]))
    assert result.code is PortFailureCode.UNAVAILABLE and result.message.startswith("resource_limit:")


@pytest.mark.parametrize("error", [OSError("disk"), MemoryError(), RuntimeError("bug")])
def test_unexpected_reader_errors_propagate_unchanged(files, monkeypatch, error):
    def raising(*args, **kwargs):
        raise error

    monkeypatch.setattr(analyzer_module, "check_resolved_wav_integrity", raising)
    with pytest.raises(type(error)) as raised:
        make(files.roots).analyze(files.request([job(SAMPLE_PEAK_DBFS, files.s1)]))
    assert raised.value is error


# --- clock and identities ---------------------------------------------------------------------------


def test_an_invalid_clock_reading_is_a_wiring_error(files):
    with pytest.raises(ValueError, match="ClockReading"):
        make(files.roots, clock=Clock(reading="noon")).analyze(files.request([job(SAMPLE_PEAK_DBFS, files.s1)]))


def test_the_clock_is_read_only_after_batch_validation(files):
    clock = Clock()
    make(files.roots, clock=clock).analyze(files.request([job(SAMPLE_PEAK_DBFS, files.s1, unit="dB")]))
    assert clock.reads == 0


def test_observation_ids_stay_fresh_across_calls_and_states(files):
    analyzer = make(files.roots)
    first = analyzer.analyze(files.request([job(SAMPLE_PEAK_DBFS, files.s1)]))
    state = files.state_of((files.s1, files.s2, files.mix))
    second = analyzer.analyze(files.request([job(SAMPLE_PEAK_DBFS, files.mix), job(SAMPLE_RMS_DBFS, files.mix)],
                                            state=state))  # fmt: skip
    ids = [o.observation_id for r in (first, second) for o in r.value.observations]
    assert len(set(ids)) == len(ids) == 3


@pytest.mark.parametrize("tokens", [["a", "a"], ["a", " "], ["a", 7]])
def test_reused_or_invalid_tokens_raise_without_regeneration(files, tokens):
    identities = Tokens(tokens)
    with pytest.raises(ValueError):
        make(files.roots, identities=identities).analyze(
            files.request([job(SAMPLE_PEAK_DBFS, files.s1), job(SAMPLE_RMS_DBFS, files.s1)])
        )
    assert identities.count == 2


def test_a_token_reused_in_a_later_call_raises(files):
    analyzer = make(files.roots, identities=Tokens(["a", "a"]))
    analyzer.analyze(files.request([job(SAMPLE_PEAK_DBFS, files.s1)]))
    with pytest.raises(ValueError, match="reused"):
        analyzer.analyze(files.request([job(SAMPLE_PEAK_DBFS, files.s1)]))


# --- one coordinator run with the real analyzer, renderer and integrity inspector --------------------


def test_coordinator_verifies_a_real_gain_change_with_real_level_measurements(tmp_path):
    # Native I/O for analysis, rendering and integrity; diagnosis and decisions are labelled controlled
    # doubles and the criteria are labelled test configuration, not justified musical thresholds.
    from test_coordinator import Clock as RunClockDouble
    from test_coordinator import Decider, Diagnosis, Identities, propose
    from test_file_run_renderer import FIXED_T1, FIXED_T2, LIMITS, PROCESSING, RECIPE, RENDER_SPEC, Project
    from test_run_recording_contracts import _configuration, _run_request

    from autonomous_mixing.adapters.journal.run_memory import InMemoryRunJournal
    from autonomous_mixing.adapters.rendering.dsp import apply_absolute_gain, sum_stems
    from autonomous_mixing.adapters.rendering.run_renderer import FileRunRenderer
    from autonomous_mixing.adapters.rendering.state_integrity import FileStateIntegrityInspector
    from autonomous_mixing.core.loop.coordinator import run
    from autonomous_mixing.core.records.acceptance import AcceptedReference
    from autonomous_mixing.core.records.admission import StemBinding
    from autonomous_mixing.core.records.analysis import deduplicate_analysis_definitions
    from autonomous_mixing.core.records.identifiers import ConditionId, CriteriaId
    from autonomous_mixing.core.records.run import RunOutcome
    from autonomous_mixing.core.records.termination import TerminationReason
    from autonomous_mixing.core.records.verification import (
        AcceptanceCriteria,
        BaselineReference,
        CheckStatus,
        ComparisonOperator,
        DeltaGuard,
        ImprovementCondition,
        ImprovementDirection,
        TargetCondition,
        Verdict,
        VerificationResult,
    )
    from autonomous_mixing.core.records.verification_assessment import ConditionMeasurement

    project = Project(tmp_path)
    rate, frames = RENDER_SPEC.sample_rate_hz, len(FIXED_T1)
    span = TimeRange(0.0, frames / rate)
    stem_scope, mix_scope = AnalysisScope((T1,), span), AnalysisScope((T1, T2), span)
    stem_peak, mix_rms = MetricSelector(SAMPLE_PEAK_DBFS, stem_scope), MetricSelector(SAMPLE_RMS_DBFS, mix_scope)

    def dbfs(value):
        return MeasuredScalar(value, "dBFS")

    # Labelled test configuration: the T1 stem peak must fall to at most 0 dBFS, improve by at least 1 dB,
    # and the mix RMS may move by at most 10 dB from the initial state.
    criteria = AcceptanceCriteria(
        CriteriaId("criteria-levels"), "1",
        (TargetCondition(ConditionId("tg"), stem_peak, ComparisonOperator.LE, dbfs(0.0)),),
        (ImprovementCondition(ConditionId("im"), stem_peak, ImprovementDirection.LOWER, dbfs(1.0)),),
        (DeltaGuard(ConditionId("dl"), mix_rms, BaselineReference.INITIAL, dbfs(10.0)),),
    )  # fmt: skip
    stem_source = SourceRequirement(ArtifactKind.STEM, (T1,), RENDER_SPEC.ref, rate, 2, 0.0, frames)
    mix_source = SourceRequirement(ArtifactKind.MIX, (T1, T2), RENDER_SPEC.ref, rate, 2, 0.0, frames)

    def definition(selector, source):
        return MeasurementDefinition(selector, "dBFS", SPEC, ANALYZER, ALIGNMENT, (source,))

    measurements = (
        ConditionMeasurement(ConditionId("tg"), definition(stem_peak, stem_source)),
        ConditionMeasurement(ConditionId("im"), definition(stem_peak, stem_source)),
        ConditionMeasurement(ConditionId("dl"), definition(mix_rms, mix_source)),
    )
    configuration = _configuration(scope=mix_scope, analysis_spec=AnalysisSpec(SPEC, (SAMPLE_PEAK_DBFS,
                                   SAMPLE_RMS_DBFS)), render_spec=RENDER_SPEC, criteria=criteria)  # fmt: skip
    request = _run_request(
        initial_state=project.state, configuration=configuration, processing_spec=PROCESSING, recipe=RECIPE,
        stem_bindings=(StemBinding(T1, ArtifactId("s1")), StemBinding(T2, ArtifactId("s2"))),
        mix_artifact_id=ArtifactId("mix"), measurements=measurements,
        analysis_definitions=deduplicate_analysis_definitions(tuple(m.definition for m in measurements)),
        root_bindings=project.bindings(project.state), output_root_key="renders",
    )  # fmt: skip

    class Recording:
        """Passes every call to the real analyzer and keeps its requests and results (no substitution)."""

        def __init__(self, inner):
            self.inner, self.calls = inner, []

        def analyze(self, analysis_request):
            result = self.inner.analyze(analysis_request)
            self.calls.append((analysis_request, result))
            return result

    analyzer = Recording(make(project.roots, identities=Tokens(prefix="level-obs")))
    before = project.source_hashes()
    report = run(
        request, analyzer=analyzer, diagnosis_source=Diagnosis(), decision_maker=Decider(propose(-6.0)),
        renderer=FileRunRenderer(project.roots, limits=LIMITS),
        integrity_inspector=FileStateIntegrityInspector(project.roots, max_file_bytes=LIMIT, max_decoded_bytes=LIMIT),
        journal=InMemoryRunJournal(request.run_id, AcceptedReference(project.state, 0)),
        clock=RunClockDouble(), identities=Identities(),
    )  # fmt: skip

    assert (report.final_run.outcome, report.final_run.termination_reason) == (
        RunOutcome.SUCCEEDED, TerminationReason.CANDIDATE_CRITERIA_MET)  # fmt: skip
    (initial_call, candidate_call) = analyzer.calls
    assert all(isinstance(result, Success) for _, result in analyzer.calls)

    def level(call, selector):
        (observation,) = [o for o in call[1].value.observations if o.metric == selector.metric]
        return observation

    # Measured values equal the analytical levels of the real files before and after the rendered gain change.
    initial_peak, candidate_peak = level(initial_call, stem_peak), level(candidate_call, stem_peak)
    assert initial_peak.result.value == pytest.approx(_db(np.max(np.abs(FIXED_T1))), rel=1e-12)
    assert candidate_peak.result.value - initial_peak.result.value == pytest.approx(-6.0, abs=1e-9)
    gained = apply_absolute_gain(FIXED_T1, -6.0)
    for call, t1 in ((initial_call, FIXED_T1), (candidate_call, gained)):
        expected = 10 * math.log10(np.mean(np.square(sum_stems((t1, FIXED_T2)))))
        assert level(call, mix_rms).result.value == pytest.approx(expected, rel=1e-12)
    candidate = candidate_call[0].state
    target = next(a for a in candidate.audio.artifacts if a.kind is ArtifactKind.STEM and a.source_track_ids == (T1,))
    assert candidate_peak.artifact_ids == (target.artifact_id,)
    assert Path(project.roots["renders"], target.location).is_file()  # measured from the rendered output root
    assert initial_peak.artifact_ids == (ArtifactId("s1"),)

    # The verifier's checks cite exactly these real observations.
    results = [e.payload for e in report.trace.events if isinstance(e.payload, VerificationResult)]
    attempt = results[-1]
    assert attempt.verdict is Verdict.ACCEPTED
    checks = {c.condition_id: c for c in attempt.checks}
    assert all(c.status is CheckStatus.PASS for c in checks.values())
    assert candidate_peak.observation_id in checks[ConditionId("tg")].evidence_ids
    assert {candidate_peak.observation_id, initial_peak.observation_id} <= set(checks[ConditionId("im")].evidence_ids)
    assert level(candidate_call, mix_rms).observation_id in checks[ConditionId("dl")].evidence_ids
    real_ids = {o.observation_id for _, r in analyzer.calls for o in r.value.observations}
    assert {i for r in results for c in r.checks for i in c.evidence_ids} <= real_ids
    assert project.source_hashes() == before  # inputs and the retained file are unchanged
