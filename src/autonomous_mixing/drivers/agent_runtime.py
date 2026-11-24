"""R5B: owned programmatic DAW agent composition, used by ``daw start`` (R5C C2). No save/export or installation.

The caller supplies producer configuration via a typed RunRequest factory. Only external transports, clock and
identities are injectable; measurement, diagnosis, audit, admission, verification and recovery remain real.
"""

import json
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace

from autonomous_mixing.adapters.audio_io.integrity import check_resolved_wav_integrity
from autonomous_mixing.adapters.audio_io.limits import AudioReadLimits
from autonomous_mixing.adapters.audio_io.locations import resolve_artifact_location
from autonomous_mixing.adapters.decision_agent.claude import ClaudeDecisionMaker
from autonomous_mixing.adapters.decision_agent.config import ClaudeConfig
from autonomous_mixing.adapters.decision_agent.prompt import PROMPT, provider_identity
from autonomous_mixing.adapters.decision_agent.response import RESPONSE_SCHEMA
from autonomous_mixing.adapters.decision_agent.transport import ClaudeMessagesTransport
from autonomous_mixing.adapters.dsp_analysis.analysis_router import AnalysisRouter
from autonomous_mixing.adapters.dsp_analysis.directional_analysis import ANALYZER as P1_ANALYZER
from autonomous_mixing.adapters.dsp_analysis.directional_analysis import DirectionalMaskingAnalyzer
from autonomous_mixing.adapters.dsp_analysis.file_analyzer import ANALYZER as LEVEL_ANALYZER
from autonomous_mixing.adapters.dsp_analysis.file_analyzer import FileAudioAnalyzer
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import (
    MaskingPreparer,
    PreparationStoreError,
    load_prepared_plan,
)
from autonomous_mixing.adapters.fl_studio.controller import FlStudioParameterController
from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
from autonomous_mixing.adapters.fl_studio.profile import calibrated_profile
from autonomous_mixing.adapters.fl_studio.transport import WinMMTransport
from autonomous_mixing.adapters.journal.decision_exchange import SqliteDecisionExchangeStore
from autonomous_mixing.adapters.journal.sqlite_run import SqliteRunJournal
from autonomous_mixing.adapters.rendering.export_handoff import FileExportHandoff
from autonomous_mixing.adapters.rendering.state_integrity import FileStateIntegrityInspector
from autonomous_mixing.adapters.rendering.workspaces import WorkspaceRoot
from autonomous_mixing.core.loop.coordinator import resume_daw, run_daw
from autonomous_mixing.core.loop.restart import reopen_daw_run
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.artifacts import ArtifactKind
from autonomous_mixing.core.records.coordinator_runtime import RunRequest
from autonomous_mixing.core.records.decision_exchange import canonical_json
from autonomous_mixing.core.records.identifiers import RunId, VersionRef
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.masking_diagnosis import (
    EXPERIMENTAL_DIAGNOSIS_METRICS,
    ExperimentalMaskingDiagnosisJob,
    ExperimentalMaskingDiagnosisPolicy,
    experimental_diagnosis_method,
)
from autonomous_mixing.core.records.masking_measurement import (
    P1_FLOAT64_PROFILE,
    MaskingMeasurementPlan,
    MaskingPreparationRequest,
    bound_specification,
)
from autonomous_mixing.core.records.observation import AnalysisScope
from autonomous_mixing.core.records.provider_run import DawAgentBinding
from autonomous_mixing.core.records.results import Success
from autonomous_mixing.core.records.run_report import RunSuspended
from autonomous_mixing.core.records.verification import MetricSelector
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition
from autonomous_mixing.drivers.daw_runtime import (
    ConfigError,
    DawToolConfig,
    SystemClock,
    UuidIdentitySource,
    load_config,
)
from autonomous_mixing.drivers.decision_evidence import ConfiguredDecisionEvidenceSource
from autonomous_mixing.drivers.diagnosis_runtime import ConfiguredDiagnosisSource


class RuntimeRefusal(ValueError):
    """A refusal caused by stored, live or file state rather than invalid input (R5C, ADR-043 Proposed).

    Existing artifacts, configuration drift from the recorded run, an unresumable classification or unverifiable
    stored evidence. It remains a ``ValueError`` so existing callers keep working; the CLI maps it to exit 4.
    """


def _positive(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate(config, run_id, provider_config, api_key):
    require_instance(config, DawToolConfig, "tool configuration")
    require_instance(run_id, RunId, "run ID")
    require_instance(provider_config, ClaudeConfig, "provider configuration")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("explicit provider credentials required")
    if load_config(config.path) != config:
        raise ValueError("tool configuration differs from the validated configuration file")
    config.store_path(run_id.value)  # existing bounded local run-ID validation


def _settings(config, run_id, max_evidence_bytes, max_audit_record_bytes, known_alignments):
    return canonical_json(
        {
            "schema": 1,
            "run_id": run_id.value,
            "run_store_dir": str(config.run_store_dir),
            "fl_journal": str(config.fl_journal),
            "session_key": config.session_key,
            "profile": [config.profile_reference.name, config.profile_reference.version],
            "midi": [config.midi_output, config.midi_input, config.timeout_s],
            "roots": [list(item) for item in config.roots],
            "read_limits": [config.max_file_bytes, config.max_decoded_bytes],
            "max_evidence_bytes": _positive(max_evidence_bytes, "evidence limit"),
            "max_audit_record_bytes": _positive(max_audit_record_bytes, "audit limit"),
            "known_alignments": sorted([m.name, m.version] for m in known_alignments),
        }
    )


class DawAgentRuntime:
    """A context manager owning one prepared run and its stores/adapters. Calling create/open sends nothing.

    A fresh create reserves new artifact directories exclusively. Failures keep diagnostic artifacts and close
    opened resources; originals are never removed. reopen accepts only the recorded suspension, never resends.
    """

    @classmethod
    def create(
        cls,
        *,
        config: DawToolConfig,
        run_id: RunId,
        preparation: MaskingPreparationRequest,
        request_factory: Callable[[MaskingMeasurementPlan, tuple[MeasurementDefinition, ...]], RunRequest],
        provider_config: ClaudeConfig,
        api_key: str,
        max_evidence_bytes: int,
        max_audit_record_bytes: int,
        known_alignments: frozenset[VersionRef],
        clock: RunClock | None = None,
        identities: IdentitySource | None = None,
        provider_transport_factory: Callable | None = None,
        fl_transport_factory: Callable | None = None,
    ) -> "DawAgentRuntime":
        _validate(config, run_id, provider_config, api_key)
        require_instance(preparation, MaskingPreparationRequest, "preparation")
        if preparation.experimental_policy is None:
            raise ValueError("an explicit experimental policy is required")
        if not callable(request_factory):
            raise ValueError("a typed request factory is required")
        if not isinstance(known_alignments, frozenset) or any(not isinstance(m, VersionRef) for m in known_alignments):
            raise ValueError("known alignments must be an explicit frozenset of method references")
        settings = _settings(config, run_id, max_evidence_bytes, max_audit_record_bytes, known_alignments)
        clock, identities = clock or SystemClock(), identities or UuidIdentitySource()
        directory = config.run_store_dir / (run_id.value + ".runtime")
        if config.store_path(run_id.value).exists() or directory.exists():
            raise RuntimeRefusal("run store or runtime artifacts already exist; never overwrite/reprepare")
        if (preparation.max_file_bytes, preparation.max_decoded_bytes) != (
            config.max_file_bytes,
            config.max_decoded_bytes,
        ):
            raise ValueError("preparation and runtime read limits differ")
        directory.mkdir()
        (directory / "plans").mkdir()
        (directory / "evidence").mkdir()
        prepared = MaskingPreparer(
            dict(config.roots), store=directory / "plans", clock=clock, known_alignments=known_alignments
        ).prepare(preparation)
        if not isinstance(prepared, Success):
            raise ConfigError(f"preparation failed: {prepared}")  # the declared inputs cannot be prepared (R5C C2)
        plan = MaskingMeasurementPlan(
            bound_specification("autonomous_mixing.daw_agent_analysis", "1", prepared.value.digest),
            P1_ANALYZER,
            prepared.value.digest,
            prepared.value.plan,
        )
        jobs = []
        for job in preparation.jobs:
            support = plan.prepared.experimental_support(job.pair, job.time_range)
            scope = AnalysisScope((job.pair.masker, job.pair.target), job.time_range)
            definitions = tuple(
                MeasurementDefinition(
                    MetricSelector(metric, scope),
                    unit,
                    plan.specification,
                    P1_ANALYZER,
                    job.alignment.method,
                    (job.masker_source, job.target_source),
                )
                for metric, unit in EXPERIMENTAL_DIAGNOSIS_METRICS
            )
            jobs.append(ExperimentalMaskingDiagnosisJob(support, definitions))
        policy = ExperimentalMaskingDiagnosisPolicy(experimental_diagnosis_method(plan), plan, tuple(jobs))
        diagnostics = tuple(d for job in jobs for d in job.measurements)
        request = request_factory(plan, diagnostics)
        require_instance(request, RunRequest, "run request")
        if (
            request.run_id != run_id
            or request.initial_state != preparation.state
            or request.provider_binding is not None
        ):
            raise ValueError("request factory changed the run/start state or supplied a provider binding")
        if request.configuration.analysis_spec.ref != plan.specification or any(
            d not in request.analysis_definitions for d in diagnostics
        ):
            raise ValueError("request must include the exact prepared diagnosis definitions and specification")
        binding = DawAgentBinding(provider_identity(provider_config), PROMPT, RESPONSE_SCHEMA, policy, settings)
        request = replace(request, provider_binding=binding)
        if plan.prepared.profile != preparation.profile:
            raise ValueError("the prepared P1 ingress profile differs from the preparation request")
        cls._preflight(config, request, plan.prepared.profile, preparation)
        instance = cls._assemble(
            config,
            request,
            directory,
            provider_config,
            api_key,
            clock,
            identities,
            provider_transport_factory,
            fl_transport_factory,
            create=True,
        )
        return instance

    @classmethod
    def check(cls, *, config: DawToolConfig, request: RunRequest, preparation: MaskingPreparationRequest) -> None:
        """The pre-write check (R5C): the existing preflight rules for an unbound request, before any preparation.

        It reads the current baseline files' headers and bytes only; it creates no directory, store, journal or
        transport. ``create`` repeats the same rules after preparation. Raises ``ValueError`` on refusal.
        """
        require_instance(config, DawToolConfig, "tool configuration")
        require_instance(request, RunRequest, "run request")
        require_instance(preparation, MaskingPreparationRequest, "preparation")
        if request.provider_binding is not None:
            raise ValueError("the pre-write check takes an unbound request; it must not carry a provider binding")
        if request.initial_state != preparation.state:
            raise ValueError("the preparation and the run start from different states")
        if (preparation.max_file_bytes, preparation.max_decoded_bytes) != (config.max_file_bytes,
                                                                            config.max_decoded_bytes):  # fmt: skip
            raise ValueError("preparation and runtime read limits differ")
        cls._preflight(config, request, preparation.profile, preparation)

    @staticmethod
    def _preflight(config, request, ingress_profile, preparation=None):
        session = request.daw_session
        if session is None or session.profile != calibrated_profile(reference=config.profile_reference):
            raise ValueError("run requires the exact currently calibrated DAW session/profile")
        roots = dict(config.roots)
        if request.output_root_key != session.export_root_key or request.output_root_key not in roots:
            raise ValueError("output root differs from the declared export root")
        if any(d.analyzer not in (P1_ANALYZER, LEVEL_ANALYZER) for d in request.analysis_definitions):
            raise ValueError("run requests an unsupported analyzer")
        if session.export_format.subtype not in (
            ("PCM_16", "DOUBLE") if ingress_profile == P1_FLOAT64_PROFILE else ("PCM_16",)
        ):
            raise ValueError("export encoding is incompatible with the prepared P1 ingress profile")
        bindings = {b.artifact_id: b.root_key for b in request.root_bindings}
        if preparation is not None and any(
            bindings.get(b.artifact_id) != b.root_key for b in preparation.root_bindings
        ):
            raise ValueError("preparation roots differ from run roots")
        affected = {session.binding.track_id, *session.routing.dependent_track_ids}
        timeline = session.export_timeline
        for artifact in request.initial_state.audio.artifacts:
            replaced = (artifact.kind is ArtifactKind.STEM and set(artifact.source_track_ids) & affected) or (
                artifact.kind is ArtifactKind.MIX and session.binding.track_id in artifact.source_track_ids
            )
            if replaced and (
                artifact.render_spec,
                artifact.sample_rate_hz,
                artifact.timeline_start_seconds,
                artifact.frame_count,
            ) != (session.render_spec, timeline.sample_rate_hz, timeline.start_seconds, timeline.frame_count):
                raise ValueError("baseline is not a native export with the session's export settings")
            if bindings[artifact.artifact_id] not in roots:
                raise ValueError("a current member has no configured runtime root")
            location = resolve_artifact_location(artifact, root=roots[bindings[artifact.artifact_id]])
            checked = check_resolved_wav_integrity(
                location, max_file_bytes=config.max_file_bytes, max_decoded_bytes=config.max_decoded_bytes
            )
            if (checked.container, checked.subtype) != (session.export_format.container, session.export_format.subtype):
                raise ValueError("baseline export settings differ from the DAW session")

    @classmethod
    def reopen(
        cls,
        *,
        config: DawToolConfig,
        run_id: RunId,
        provider_config: ClaudeConfig,
        api_key: str,
        clock: RunClock | None = None,
        identities: IdentitySource | None = None,
        provider_transport_factory: Callable | None = None,
        fl_transport_factory: Callable | None = None,
    ) -> "DawAgentRuntime":
        _validate(config, run_id, provider_config, api_key)
        clock, identities = clock or SystemClock(), identities or UuidIdentitySource()
        journal = SqliteRunJournal.open(config.store_path(run_id.value), run_id=run_id)
        try:
            snapshot = journal.snapshot()
            if not isinstance(snapshot, Success) or not snapshot.value.events:
                raise RuntimeRefusal("an unstarted/unavailable run is not resumable")
            request = snapshot.value.events[0].payload
            if not isinstance(request, RunRequest) or not isinstance(request.provider_binding, DawAgentBinding):
                raise RuntimeRefusal("run has no owned runtime configuration")
            binding = request.provider_binding
            local = json.loads(binding.runtime_settings)
            expected = _settings(
                config,
                run_id,
                local["max_evidence_bytes"],
                local["max_audit_record_bytes"],
                frozenset(VersionRef(*m) for m in local["known_alignments"]),
            )
            if expected != binding.runtime_settings or (binding.provider, binding.prompt, binding.response_schema) != (
                provider_identity(provider_config),
                PROMPT,
                RESPONSE_SCHEMA,
            ):
                raise RuntimeRefusal("runtime/provider configuration drifted from the recorded run")
            directory = config.run_store_dir / (run_id.value + ".runtime")
            instance = cls._assemble(
                config,
                request,
                directory,
                provider_config,
                api_key,
                clock,
                identities,
                provider_transport_factory,
                fl_transport_factory,
                create=False,
                journal=journal,
            )
        except BaseException:
            journal.close()
            raise
        try:
            problem = instance.evidence.verify_run(snapshot.value)
            if problem is not None:
                raise RuntimeRefusal(f"stored evidence cannot be verified: {problem}")
            suspended = reopen_daw_run(
                instance.journal, instance.controller, identities=identities, clock=clock, record_interruption=False
            )
            if not isinstance(suspended, RunSuspended):
                raise RuntimeRefusal(f"run is not resumable: {suspended}")
            instance.suspended = suspended
            return instance
        except BaseException:
            instance.close()
            raise

    @classmethod
    def _assemble(
        cls,
        config,
        request,
        directory,
        provider_config,
        api_key,
        clock,
        identities,
        provider_transport_factory,
        fl_transport_factory,
        *,
        create,
        journal=None,
    ):
        stack = ExitStack()
        try:
            self = cls()
            self._stack, self._closed = stack, False
            self.request, self.config, self.clock, self.identities = request, config, clock, identities
            self.suspended = None
            settings = json.loads(request.provider_binding.runtime_settings)
            policy = request.provider_binding.diagnosis_policy
            plan = policy.measurement_plan
            try:
                stored = load_prepared_plan(directory / "plans", plan.prepared_digest)
            except (PreparationStoreError, OSError) as error:  # never regenerated in its place
                raise RuntimeRefusal(f"the stored preparation manifest is unusable: {error}") from error
            if stored != plan.prepared:
                raise RuntimeRefusal("the stored preparation manifest is unusable: it differs from the run")
            self.journal = journal or SqliteRunJournal.create(
                config.store_path(request.run_id.value), request.run_id, AcceptedReference(request.initial_state, 0)
            )
            stack.callback(self.journal.close)
            self.audit = (SqliteDecisionExchangeStore.create if create else SqliteDecisionExchangeStore.open)(
                directory / "decisions.sqlite",
                run_id=request.run_id,
                max_record_bytes=settings["max_audit_record_bytes"],
            )
            stack.callback(self.audit.close)
            read = dict(max_file_bytes=config.max_file_bytes, max_decoded_bytes=config.max_decoded_bytes)
            roots = dict(config.roots)
            self.masking = DirectionalMaskingAnalyzer(
                roots,
                plans=(plan,),
                plan_store=directory / "plans",
                evidence_store=directory / "evidence",
                max_evidence_bytes=settings["max_evidence_bytes"],
                clock=clock,
                identities=identities,
                **read,
            )
            self.analyzer = AnalysisRouter(
                {
                    P1_ANALYZER: self.masking,
                    LEVEL_ANALYZER: FileAudioAnalyzer(roots, clock=clock, identities=identities, **read),
                }
            )
            self.diagnosis = ConfiguredDiagnosisSource(policy=policy, identities=identities)
            self.evidence = ConfiguredDecisionEvidenceSource(
                binding=request.provider_binding,
                config=provider_config,
                audit=self.audit,
                analysis_verifier=self.masking,
            )
            self.inspector = FileStateIntegrityInspector(roots, **read)
            self.fl_journal = SqliteCommandJournal(config.fl_journal, session_key=config.session_key)
            stack.callback(self.fl_journal.close)
            factory = (
                (lambda: fl_transport_factory(self.fl_journal))
                if fl_transport_factory
                else (lambda: WinMMTransport(config.midi_output, config.midi_input))
            )
            self.controller = FlStudioParameterController(
                profile=request.daw_session.profile,
                journal=self.fl_journal,
                transport_factory=factory,
                timeout_s=config.timeout_s,
                clock=lambda: clock.read().monotonic_seconds,
                next_inspect_id=lambda: "inspect_" + identities.next_token(),
            )
            transport = (provider_transport_factory or (lambda cfg, key: ClaudeMessagesTransport(cfg, api_key=key)))(
                provider_config, api_key
            )
            stack.callback(transport.close)
            self.decision_maker = ClaudeDecisionMaker(
                config=provider_config, transport=transport, audit=self.audit, identities=identities, clock=clock
            )
            self.export_handoff = FileExportHandoff(
                root=WorkspaceRoot(request.output_root_key, roots[request.output_root_key]),
                inventory=self._inventory,
                limits=AudioReadLimits(config.max_file_bytes, config.max_decoded_bytes),
            )
            return self
        except BaseException:
            stack.close()
            raise

    def _inventory(self, checkpoint):
        from autonomous_mixing.core.records.daw_correction import DawCorrectionSuspended

        snapshot = self.journal.snapshot()
        if not isinstance(snapshot, Success):
            raise ValueError("run inventory is unavailable")
        snapshot = snapshot.value
        states = [self.request.initial_state, *(a.accepted.state for a in snapshot.acceptances)]
        roots = {b.artifact_id: b.root_key for b in self.request.root_bindings}
        for event in snapshot.events:
            if isinstance(event.payload, DawCorrectionSuspended):
                for output in event.payload.awaiting.request.outputs:
                    roots[output.artifact_id] = self.request.output_root_key
        matches = [s for s in states if s.audio == checkpoint]
        if len(matches) != 1:
            return None
        state = matches[0]
        artifacts = {
            a.artifact_id: a
            for a in (*state.audio.artifacts, *(g.input_artifact for g in state.processing.track_gains))
        }
        locations = tuple(
            resolve_artifact_location(a, root=dict(self.config.roots)[roots[a.artifact_id]]) for a in artifacts.values()
        )
        return state, locations

    def _collaborators(self):
        if self._closed:
            raise ValueError("runtime is closed")
        return dict(
            analyzer=self.analyzer,
            diagnosis_source=self.diagnosis,
            decision_maker=self.decision_maker,
            integrity_inspector=self.inspector,
            journal=self.journal,
            clock=self.clock,
            identities=self.identities,
            daw_controller=self.controller,
            recovery_controller=self.controller,
            export_handoff=self.export_handoff,
            decision_evidence=self.evidence,
        )

    def start(self):
        result = run_daw(self.request, **self._collaborators())
        self.suspended = result if isinstance(result, RunSuspended) else None
        return result

    def continue_export(self, suspended, manifest):
        if suspended.request != self.request:
            raise ValueError("continuation request differs from this runtime")
        result = resume_daw(suspended, manifest, **self._collaborators())
        self.suspended = result if isinstance(result, RunSuspended) else None
        return result

    def close(self):
        if not self._closed:
            self._closed = True
            self._stack.close()

    def __enter__(self):
        if self._closed:
            raise ValueError("runtime is closed")
        return self

    def __exit__(self, *args):
        self.close()
