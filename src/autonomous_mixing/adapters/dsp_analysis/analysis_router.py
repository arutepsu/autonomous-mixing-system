"""Mixed-job ``AudioAnalyzer`` dispatch by declared analyzer identity (R1 Task 4).

``AnalysisRouter`` holds one ``AudioAnalyzer`` per analyzer identity (for example the level analyzer
``autonomous_mixing.file_level_analyzer@1`` and the P1 directional analyzer) and splits a request by each job's
``definition.analyzer``:

1. Request type (``ValueError`` otherwise). Any job whose analyzer is not routed fails the whole batch as
   ``UNSUPPORTED`` before any analyzer is called.
2. One subrequest per routed analyzer, in first-occurrence order of the jobs: the same state, scope, specification
   reference and exact root bindings, only that analyzer's definitions (original order) and only their metric IDs
   (in the original specification order). Each analyzer validates its own subrequest.
3. Any sub-failure is returned unchanged (prefixed with the analyzer identity) and no observation is returned; there
   is no partial success. Each successful subresponse is first validated against its own subrequest
   (``validate_analysis_response``); an invalid one is ``INVALID_RESPONSE`` attributed to that analyzer, and later
   analyzers are not called. Analyzers called earlier may already have produced observations or evidence; they are
   discarded, not merged.
4. Observations are merged in the original job order. An observation whose ``analyzer`` differs from its job's
   declared analyzer (relabeling), duplicated observation IDs across sub-batches, or a merged response that fails
   ``validate_analysis_response`` against the **original** request return ``INVALID_RESPONSE``.

The router computes nothing and relabels nothing: each observation keeps the identity of the analyzer that produced
it. It does not make an input valid for an analyzer that refuses it (for example stereo for P1).
"""

from collections.abc import Mapping

from autonomous_mixing.core.ports.analysis import AudioAnalyzer, validate_analysis_response
from autonomous_mixing.core.records.analysis import AnalysisRequest, AnalysisResponse
from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.observation import AnalysisScope, AnalysisSpec, Observation
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, PortResult, Success


def _name(ref: VersionRef) -> str:
    return f"{ref.name}@{ref.version}"


class AnalysisRouter:
    """Implements ``AudioAnalyzer`` by dispatching each job to the analyzer its definition names."""

    def __init__(self, routes: Mapping[VersionRef, AudioAnalyzer]) -> None:
        if not isinstance(routes, Mapping) or not routes:
            raise ValueError("routes must be a nonempty Mapping of analyzer identity to AudioAnalyzer")
        snapshot = dict(routes.items())
        for ref, analyzer in snapshot.items():
            require_instance(ref, VersionRef, "route identity")
            if not callable(getattr(analyzer, "analyze", None)):
                raise ValueError(f"route {_name(ref)} must provide analyze()")
        if len({id(a) for a in snapshot.values()}) != len(snapshot):
            raise ValueError("one analyzer instance may serve only one identity (no aliasing)")
        self._routes = snapshot

    def analyze(self, request: AnalysisRequest) -> PortResult[AnalysisResponse]:
        require_instance(request, AnalysisRequest, "request")
        unrouted = sorted({_name(d.analyzer) for d in request.definitions if d.analyzer not in self._routes})
        if unrouted:
            return PortFailure(PortFailureCode.UNSUPPORTED, f"no analyzer is routed for {', '.join(unrouted)}", False)
        order = list(dict.fromkeys(d.analyzer for d in request.definitions))
        produced: dict[tuple[VersionRef, AnalysisScope], Observation] = {}
        evidence = []
        for ref in order:
            definitions = tuple(d for d in request.definitions if d.analyzer == ref)
            used = {d.selector.metric for d in definitions}
            subrequest = AnalysisRequest(
                request.state,
                request.scope,
                AnalysisSpec(
                    request.specification.ref, tuple(m for m in request.specification.metric_ids if m in used)
                ),
                definitions,
                request.root_bindings,
            )
            result = self._routes[ref].analyze(subrequest)
            if isinstance(result, PortFailure):
                return PortFailure(result.code, f"{_name(ref)}: {result.message}", result.retryable)
            if not isinstance(result, Success) or not isinstance(result.value, AnalysisResponse):
                return PortFailure(
                    PortFailureCode.INVALID_RESPONSE, f"{_name(ref)} returned no AnalysisResponse", False
                )
            try:
                validate_analysis_response(subrequest, result.value)
            except ValueError as error:
                return PortFailure(PortFailureCode.INVALID_RESPONSE, f"{_name(ref)}: invalid subresponse: {error}",
                                   False)  # fmt: skip
            evidence.extend(result.value.evidence)
            for observation in result.value.observations:
                if observation.analyzer != ref:
                    return PortFailure(PortFailureCode.INVALID_RESPONSE,
                                       f"{_name(ref)} returned an observation labelled {_name(observation.analyzer)}",
                                       False)  # fmt: skip
                key = (observation.metric, observation.scope)
                if key in produced:
                    return PortFailure(PortFailureCode.INVALID_RESPONSE,
                                       f"{_name(ref)} returned a duplicate observation", False)  # fmt: skip
                produced[key] = observation
        jobs = [(d.selector.metric, d.selector.scope) for d in request.definitions]
        rank = {key: index for index, key in enumerate(jobs)}
        # Unrequested observations are kept (after the requested ones) so that validation rejects them.
        ordered = sorted(produced.items(), key=lambda item: rank.get(item[0], len(jobs)))
        try:
            merged = AnalysisResponse(tuple(observation for _, observation in ordered), tuple(evidence))
            validate_analysis_response(request, merged)
        except ValueError as error:
            return PortFailure(PortFailureCode.INVALID_RESPONSE, f"merged response is invalid: {error}", False)
        return Success(merged)
