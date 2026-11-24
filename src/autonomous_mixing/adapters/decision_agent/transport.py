"""Single-attempt SDK transport with exact application bytes and bounded raw HTTP bodies.

No SDK import occurs until construction. HTTP entity bytes (identity encoding requested) are captured before SDK
error handling can buffer them. This is not a packet capture, TLS evidence or a total blocking-I/O deadline.
"""

import math
import time
from dataclasses import dataclass

from autonomous_mixing.adapters.decision_agent.config import ClaudeConfig


@dataclass(frozen=True)
class TransportResult:
    body: bytes | None
    status: int | None
    request_id: str | None
    sdk_version: str
    error_code: str | None = None
    retryable: bool = False


class ClaudeMessagesTransport:
    """Owns one SDK/HTTP client. Caller closes it; calls are synchronous, not thread safe.

    The optional HTTP transport is for an explicit composition/test seam, not a provider/model fallback.
    SDK retries, HTTP redirects, environment proxies and SDK config-file discovery are disabled.
    """

    def __init__(self, config: ClaudeConfig, *, api_key: str, http_transport=None):
        if not isinstance(config, ClaudeConfig):
            raise ValueError("config must be ClaudeConfig")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("explicit credentials required")
        import anthropic
        import httpx2

        self.config = config
        self._anthropic, self._http = anthropic, httpx2
        self._capture = None
        inner = http_transport if http_transport is not None else httpx2.HTTPTransport(retries=0)
        owner = self

        class Capture(httpx2.BaseTransport):
            def handle_request(self, request):
                try:
                    # Override SDK environment-derived headers at the final HTTP boundary.
                    # Only these explicit public-profile headers and the injected credential are sent.
                    request.headers.clear()
                    request.headers.update(
                        {
                            "host": "api.anthropic.com",
                            "content-type": "application/json",
                            "accept": "application/json",
                            "accept-encoding": "identity",
                            "content-length": str(len(request.content)),
                            "anthropic-version": config.api_version,
                            "x-api-key": api_key,
                        }
                    )
                    original = inner.handle_request(request)
                    owner._capture = TransportResult(
                        b"", original.status_code, original.headers.get("request-id"), anthropic.__version__
                    )
                    body = bytearray()
                    error, retryable = None, False
                    try:
                        chunks = (original.content,) if original.is_stream_consumed else original.iter_raw()
                        for chunk in chunks:
                            remaining = config.max_response_bytes + 1 - len(body)
                            body.extend(chunk[:remaining])
                            if len(body) > config.max_response_bytes:
                                error = "response_too_large"
                                break
                            if time.monotonic() > owner._deadline:
                                error, retryable = "response_deadline", True
                                break
                    except httpx2.TransportError:
                        error, retryable = "response_read_failed", True
                    finally:
                        original.close()
                    if original.headers.get("content-encoding", "identity") != "identity":
                        error, retryable = "unsupported_content_encoding", False
                    owner._capture = TransportResult(
                        bytes(body),
                        original.status_code,
                        original.headers.get("request-id"),
                        anthropic.__version__,
                        error,
                        retryable,
                    )
                    # Do not reintroduce a content decoder or obsolete wire length into the SDK's bounded copy.
                    headers = {
                        k: v
                        for k, v in original.headers.items()
                        if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
                    }
                    return httpx2.Response(original.status_code, headers=headers, content=bytes(body))
                except httpx2.TransportError:
                    raise
                except Exception as exc:
                    owner._unexpected = exc
                    raise

            def close(self):
                inner.close()

        client = httpx2.Client(transport=Capture(), follow_redirects=False, trust_env=False)
        self._client = anthropic.Anthropic(
            api_key=api_key,
            max_retries=0,
            http_client=client,
            base_url="https://api.anthropic.com",
            timeout=config.timeout_seconds,
        )

    def send(self, body: bytes, *, timeout_seconds: float) -> TransportResult:
        if not isinstance(body, bytes) or len(body) > self.config.max_request_bytes:
            raise ValueError("request bytes exceed configured limit")
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= self.config.timeout_seconds
        ):
            raise ValueError("invalid request timeout")
        self._capture, self._unexpected = None, None
        self._deadline = time.monotonic() + timeout_seconds
        try:
            response = self._client.post(
                "/v1/messages",
                cast_to=self._http.Response,
                content=body,
                stream=True,
                options={
                    "timeout": timeout_seconds,
                    "headers": {
                        "content-type": "application/json",
                        "accept-encoding": "identity",
                        "anthropic-version": self.config.api_version,
                    },
                },
            )
            response.close()
        except self._anthropic.APIStatusError:
            pass  # Exact received bytes/status were already captured, including bounded error bodies.
        except self._anthropic.APIConnectionError as error:
            if self._unexpected is not None:
                raise self._unexpected
            # SDK wraps exceptions from request preparation/client.send as well as network failures.
            # Only a genuine HTTP transport failure is an expected retryable outage.
            cause = error.__cause__
            if not isinstance(cause, self._http.TransportError):
                if cause is not None:
                    raise cause
                raise
            if self._capture is None:
                return TransportResult(None, None, None, self._anthropic.__version__, "connection_or_timeout", True)
        if self._capture is None:
            raise RuntimeError("SDK returned without a captured HTTP response")
        result = self._capture
        if result.error_code is not None:
            return result
        if not 200 <= result.status < 300:
            return TransportResult(
                result.body,
                result.status,
                result.request_id,
                result.sdk_version,
                f"http_{result.status}",
                result.status in (408, 409, 429) or result.status >= 500,
            )
        return result

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
