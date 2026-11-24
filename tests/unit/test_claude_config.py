"""R4 tests: structural fixtures only, no model call or diagnosis claim."""

import json

import pytest
from decision_support import SENTINELS, UTC, situation

from autonomous_mixing.adapters.decision_agent.config import ClaudeConfig
from autonomous_mixing.adapters.decision_agent.prompt import build_claude_request


def config(**changes):
    return ClaudeConfig(
        **(
            dict(
                model="explicit-test-model",
                api_version="2023-06-01",
                max_tokens=1024,
                temperature=0.0,
                timeout_seconds=20.0,
                max_request_bytes=200000,
                max_response_bytes=50000,
            )
            | changes
        )
    )


@pytest.mark.parametrize(
    "change",
    [
        dict(model=""),
        dict(api_version="unknown"),
        dict(max_tokens=True),
        dict(temperature=float("nan")),
        dict(temperature=2.0),
        dict(timeout_seconds=0),
        dict(max_request_bytes=False),
        dict(max_response_bytes=-1),
    ],
)
def test_configuration_rejects_invalid_values(change):
    with pytest.raises(ValueError):
        config(**change)


def test_complete_request_is_public_bound_and_versioned():
    request = build_claude_request(situation(), config=config(), exchange_id="x", requested_at=UTC)
    body = json.loads(request.request_text)
    assert body["model"] == "explicit-test-model"
    assert body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": request.packet.payload}]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "absolute" in body["system"]
    assert "abstain" in body["system"]
    assert "tools" not in body and "thinking" not in body
    assert request.request_sha256 != request.packet.payload_sha256
    assert all(value not in request.request_text for value in SENTINELS)
    assert request.packet.binding.withheld
    assert not hasattr(config(), "api_key")


def test_complete_request_bound_includes_prompt_and_schema():
    a = build_claude_request(situation(), config=config(), exchange_id="x", requested_at=UTC)
    b = build_claude_request(situation(), config=config(max_tokens=100), exchange_id="x", requested_at=UTC)
    assert a.request_sha256 != b.request_sha256
    assert json.loads(a.request_text)["output_config"]["format"]["schema"]


def test_full_request_limit_is_not_only_the_packet_limit():
    request = build_claude_request(situation(), config=config(), exchange_id="x", requested_at=UTC)
    size = len(request.request_text.encode())
    assert build_claude_request(situation(), config=config(max_request_bytes=size), exchange_id="x", requested_at=UTC)
    with pytest.raises(ValueError):
        build_claude_request(situation(), config=config(max_request_bytes=size - 1), exchange_id="x", requested_at=UTC)
