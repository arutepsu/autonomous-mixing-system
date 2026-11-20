"""One exchange, with controlled clocks and byte streams; no devices."""

import json

import pytest

from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.adapters.fl_studio.exchange import exchange


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Transport:
    def __init__(self, clock, replies=()):
        self.clock, self.replies, self.sent = clock, list(replies), []

    def send(self, raw):
        self.sent.append(raw)

    def receive(self, remaining):
        self.clock.now += min(0.01, remaining)
        return self.replies.pop(0) if self.replies else None


def command():
    return protocol.inspect_command("exchange_inspect_01", "Thesis Bass Bridge Test", 2, "Bass")


def reply(id="exchange_inspect_01"):
    observed = dict(
        api=45,
        fl_version="Producer Edition v26.1.7 [build 5654]",
        project_title="Thesis Bass Bridge Test",
        insert=dict(index=2, name="Bass"),
        gain=dict(normalized=0.76, db=-1.1527416706085205),
    )
    return protocol.encode(protocol.reply(id, "ok", observed))


def test_valid_reply_and_no_retries():
    clock = Clock()
    transport = Transport(clock, [reply()])
    result = exchange(command(), transport=transport, timeout_s=1, clock=clock)
    assert result.status == "replied"
    assert json.loads(result.reply_json)["status"] == "ok"
    assert len(transport.sent) == 1


def test_foreign_and_undecodable_frames_do_not_displace_matching_reply():
    clock = Clock()
    transport = Transport(clock, [b"not midi", b"\xf0\x7dgarbage\xf7", reply("other_command_01"), reply()])
    result = exchange(command(), transport=transport, timeout_s=1, clock=clock)
    assert result.status == "replied"
    assert len(result.diagnostics) == 2


def test_malformed_matching_reply_stops_before_later_good_reply():
    clock = Clock()
    bad = protocol.decode(reply())
    bad["observed"]["gain"]["normalized"] = 2
    raw = b"\xf0\x7d" + json.dumps(bad).encode() + b"\xf7"
    transport = Transport(clock, [raw, reply()])
    result = exchange(command(), transport=transport, timeout_s=1, clock=clock)
    assert result.status == "invalid_reply"
    assert len(transport.replies) == 1


def test_inconsistent_matching_identity_is_invalid():
    clock = Clock()
    message = protocol.decode(reply())
    message["observed"]["insert"]["name"] = "Wrong"
    result = exchange(command(), transport=Transport(clock, [protocol.encode(message)]), timeout_s=1, clock=clock)
    assert result.status == "invalid_reply"


def test_deadline_and_diagnostics_are_bounded():
    clock = Clock()
    transport = Transport(clock, [b"\xf0\x7dgarbage\xf7"] * 1000)
    result = exchange(command(), transport=transport, timeout_s=2, clock=clock)
    assert result.status == "timeout"
    assert clock.now == 2
    assert len(result.diagnostics) == 32
    assert all(len(entry) <= 1024 for entry in result.diagnostics)


@pytest.mark.parametrize("value", [True, False, 0, -1, float("inf"), float("nan"), "1"])
def test_invalid_timeout_sends_nothing(value):
    clock = Clock()
    transport = Transport(clock)
    with pytest.raises(ValueError):
        exchange(command(), transport=transport, timeout_s=value, clock=clock)
    assert not transport.sent


@pytest.mark.parametrize("method", ["send", "receive"])
def test_transport_exception_propagates_to_owner(method):
    clock = Clock()
    transport = Transport(clock)

    def fail(*args):
        raise OSError("controlled failure")

    setattr(transport, method, fail)
    with pytest.raises(OSError):
        exchange(command(), transport=transport, timeout_s=1, clock=clock)
