"""Explicit engineering configuration; credentials are a separate transport dependency."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ClaudeConfig:
    model: str
    api_version: str
    max_tokens: int
    temperature: float
    timeout_seconds: float
    max_request_bytes: int
    max_response_bytes: int

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.strip() or self.model != self.model.strip():
            raise ValueError("model must be an explicit nonblank identifier")
        if self.api_version != "2023-06-01":
            raise ValueError("unsupported API profile")
        for name in ("max_tokens", "max_request_bytes", "max_response_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive int")
        for name in ("temperature", "timeout_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0 <= self.temperature <= 1 or self.timeout_seconds <= 0:
            raise ValueError("temperature or timeout outside supported range")
