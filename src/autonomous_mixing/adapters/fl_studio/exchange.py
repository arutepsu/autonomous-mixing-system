"""One request/reply exchange, without retries, journal state or DAW authority."""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass

from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.adapters.fl_studio.transport import MidiTransport


@dataclass(frozen=True)
class ExchangeResult:
    status: str
    reply_json: str | None
    diagnostics: tuple[str, ...]


def exchange(
    command: dict, *, transport: MidiTransport, timeout_s: float, clock: Callable[[], float]
) -> ExchangeResult:
    """Send once; a malformed matching reply ends the exchange immediately. Deadlines are cooperative."""
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (float, int))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise ValueError("timeout_s must be finite and positive")
    protocol.validate_command(command)
    deadline = clock() + timeout_s
    diagnostics = []

    def note(message):
        if len(diagnostics) < 32:
            diagnostics.append(str(message)[:1024])

    def result(status, reply=None):
        return ExchangeResult(
            status, json.dumps(reply, allow_nan=False, sort_keys=True) if reply else None, tuple(diagnostics)
        )

    transport.send(protocol.encode(command))
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return result("timeout")
        raw = transport.receive(remaining)
        if raw is None or not protocol.is_bridge_sysex(raw):
            continue
        try:
            decoded = protocol.decode(raw)
        except protocol.ProtocolError as error:
            note(error)
            continue
        mine = decoded.get("id") == command["id"]
        try:
            protocol.validate_reply(decoded)
            problem = protocol.reply_consistency(command, decoded) if mine else None
        except protocol.ProtocolError as error:
            problem = error.code
        if problem:
            note(problem)
            if mine:
                return result("invalid_reply")
        elif mine:
            return result("replied", decoded)
        else:
            note("stray reply: " + str(decoded.get("id")))
