"""R5C run-configuration fixtures built from the R5B numerical setup (labelled test data, not evaluation data)."""

import json
from types import SimpleNamespace

from agent_runtime_support import LIMIT, Setup, provider_config
from masking_support import ALIGNMENT

from autonomous_mixing.adapters.journal.record_codec import encode
from autonomous_mixing.core.records.identifiers import VersionRef

DEFINITION_NAMES = ("s1-sample-peak", "mix-sample-peak")
SESSION_FIELDS = (
    "binding",
    "permission",
    "routing",
    "export_format",
    "export_timeline",
    "render_spec",
    "export_root_key",
)


def expected_request(setup: Setup, specification: VersionRef, diagnostics=()):
    """The R5B fixture's own typed request for ``specification`` (the reference the run file must reproduce)."""
    return setup.request(SimpleNamespace(specification=specification), diagnostics)


def run_config_data(setup: Setup) -> dict:
    """The run-configuration envelope (schema 1) declaring exactly the R5B fixture's run and preparation."""
    request = expected_request(setup, VersionRef("fixture-placeholder", "1"))
    configuration = request.configuration
    names = dict(zip(request.analysis_definitions, DEFINITION_NAMES, strict=True))
    session = request.daw_session
    provider = provider_config()
    preparation = setup.preparation
    return {
        "schema": 1,
        "kind": "autonomous_mixing.daw_agent_run",
        "run": {
            "initial_state": encode(request.initial_state),
            "root_bindings": encode(request.root_bindings),
            "scope": encode(configuration.scope),
            "render_spec": encode(configuration.render_spec),
            "context": encode(configuration.context),
            "criteria": encode(configuration.criteria),
            "controller": encode(configuration.controller),
            "budget_limits": encode(configuration.budget_limits),
            "retry_policy": encode(request.retry_policy),
            "policy": encode(request.policy),
            "renderer_capabilities": encode(request.renderer_capabilities),
            "processing_spec": encode(request.processing_spec),
            "recipe": encode(request.recipe),
            "stem_bindings": encode(request.stem_bindings),
            "mix_artifact_id": encode(request.mix_artifact_id),
            "alignment": encode(request.alignment),
            "definitions": [
                {
                    "name": names[d],
                    "selector": encode(d.selector),
                    "unit": d.unit,
                    "analyzer": encode(d.analyzer),
                    "alignment": encode(d.alignment),
                    "sources": encode(d.sources),
                }
                for d in request.analysis_definitions
            ],
            "measurements": [
                {"condition_id": encode(m.condition_id), "definition": names[m.definition]}
                for m in request.measurements
            ],
            "daw_session": {name: encode(getattr(session, name)) for name in SESSION_FIELDS},
        },
        "preparation": {
            "profile": encode(preparation.profile),
            "jobs": encode(preparation.jobs),
            "experimental_policy": encode(preparation.experimental_policy),
            "max_elapsed_seconds": preparation.max_elapsed_seconds,
            "known_alignments": [encode(ALIGNMENT.method)],
        },
        "provider": {
            name: getattr(provider, name)
            for name in (
                "model",
                "api_version",
                "max_tokens",
                "temperature",
                "timeout_seconds",
                "max_request_bytes",
                "max_response_bytes",
            )
        },
        "limits": {"max_evidence_bytes": LIMIT, "max_audit_record_bytes": LIMIT},
    }


def write_run_config(setup: Setup, data: dict | None = None, *, text: str | None = None):
    path = setup.root / "run.json"
    path.write_text(text if text is not None else json.dumps(data or run_config_data(setup)), encoding="utf-8")
    return path
