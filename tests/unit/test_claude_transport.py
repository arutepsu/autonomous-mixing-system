"""Locked SDK transport tested against an in-memory HTTP backend only."""

import pytest

httpx = pytest.importorskip("httpx2", reason="install the locked agent extra for SDK transport tests")
from test_claude_config import config  # noqa: E402

from autonomous_mixing.adapters.decision_agent.transport import ClaudeMessagesTransport  # noqa: E402

KEY = "test-only-do-not-log"
BODY = b'{"model":"explicit-test-model","stream":false}'


def transport(handler, **changes):
    return ClaudeMessagesTransport(config(**changes), api_key=KEY, http_transport=httpx.MockTransport(handler))


def test_exact_request_bytes_headers_and_one_attempt():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.content == BODY
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"request-id": "req-1"}, content=b'{"x":1}')

    with transport(handler) as t:
        result = t.send(BODY, timeout_seconds=2)
    assert len(seen) == 1
    assert result.body == b'{"x":1}' and result.request_id == "req-1"
    assert result.status == 200 and result.error_code is None
    assert result.sdk_version == "1.13.0"
    assert KEY not in repr(result)


@pytest.mark.parametrize(
    "status,retryable",
    [
        (400, False),
        (401, False),
        (403, False),
        (404, False),
        (408, True),
        (409, True),
        (429, True),
        (500, True),
        (503, True),
        (302, False),
    ],
)
def test_http_failure_is_not_retried_or_redirected(status, retryable):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://example.invalid/other"}, content=b'{"error":"no"}')

    with transport(handler) as t:
        result = t.send(BODY, timeout_seconds=1)
    assert len(calls) == 1
    assert result.status == status and result.body == b'{"error":"no"}'
    assert result.retryable is retryable


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_network_failure_has_no_invented_response(error):
    calls = []

    def handler(request):
        calls.append(request)
        raise error("secret local path must not escape", request=request)

    with transport(handler) as t:
        result = t.send(BODY, timeout_seconds=1)
    assert len(calls) == 1 and result.body is None
    assert result.retryable and result.error_code
    assert "secret" not in repr(result)


@pytest.mark.parametrize("status", [200, 429, 500])
def test_response_bound_applies_before_sdk_reads_errors(status):
    consumed = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for i in range(100):
                consumed.append(i)
                yield b"x" * 10

    with transport(lambda r: httpx.Response(status, stream=Stream()), max_response_bytes=20) as t:
        result = t.send(BODY, timeout_seconds=1)
    assert len(result.body) == 21
    assert result.error_code == "response_too_large"
    assert result.retryable is False
    assert len(consumed) == 3


def test_partial_read_is_preserved_and_not_a_complete_body():
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadError("private")

    with transport(lambda r: httpx.Response(200, stream=Stream())) as t:
        result = t.send(BODY, timeout_seconds=1)
    assert result.body == b"partial" and result.error_code == "response_read_failed"
    assert result.retryable


def test_credentials_and_limits_refuse_before_send():
    with pytest.raises(ValueError):
        ClaudeMessagesTransport(config(), api_key="")
    calls = []
    with transport(lambda r: calls.append(r)) as t:
        with pytest.raises(ValueError):
            t.send(BODY, timeout_seconds=0)
        with pytest.raises(ValueError):
            t.send(b"x" * 200001, timeout_seconds=1)
    assert not calls


def test_programming_error_in_transport_propagates():
    def broken(request):
        raise RuntimeError("bug")

    with transport(broken) as t:
        with pytest.raises(RuntimeError, match="bug"):
            t.send(BODY, timeout_seconds=1)


def test_sdk_environment_cannot_override_explicit_auth_or_public_headers(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "other-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv(
        "ANTHROPIC_CUSTOM_HEADERS", "X-API-Key: other-key\nAuthorization: Bearer private\nX-Private: secret"
    )

    def handler(request):
        assert request.url.host == "api.anthropic.com"
        assert request.headers["x-api-key"] == KEY
        assert "authorization" not in request.headers and "x-private" not in request.headers
        return httpx.Response(200, content=b"ok")

    with transport(handler) as t:
        assert t.send(BODY, timeout_seconds=1).body == b"ok"


def test_compressed_response_is_bounded_raw_and_never_decoded():
    import gzip

    compressed = gzip.compress(b"x" * 100000)

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield compressed

    with transport(lambda r: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Stream())) as t:
        result = t.send(BODY, timeout_seconds=1)
    assert result.body == compressed and result.error_code == "unsupported_content_encoding"


def test_compressed_response_does_not_invoke_a_decoder(monkeypatch):
    import gzip

    from httpx2._decoders import GZipDecoder

    compressed = gzip.compress(b"x" * 100000)

    def forbidden(*args):
        raise AssertionError("compressed body decoded before rejection")

    monkeypatch.setattr(GZipDecoder, "decode", forbidden)

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield compressed

    with transport(lambda r: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Stream())) as t:
        assert t.send(BODY, timeout_seconds=1).error_code == "unsupported_content_encoding"


def test_closed_sdk_client_is_not_a_retryable_provider_failure():
    t = transport(lambda r: httpx.Response(200, content=b"ok"))
    t.close()
    with pytest.raises(RuntimeError, match="closed"):
        t.send(BODY, timeout_seconds=1)


def test_sdk_programming_error_before_capture_propagates_the_same_object(monkeypatch):
    error = RuntimeError("request preparation bug")

    def broken(*args, **kwargs):
        raise error

    with transport(lambda r: httpx.Response(200, content=b"ok")) as t:
        monkeypatch.setattr(httpx.Client, "send", broken)
        with pytest.raises(RuntimeError) as caught:
            t.send(BODY, timeout_seconds=1)
    assert caught.value is error
