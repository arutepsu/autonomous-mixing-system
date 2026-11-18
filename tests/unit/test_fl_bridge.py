"""Simulated tests for the FL Studio command bridge (``scripts/fl_studio/bridge_*.py``, ``device_bridge.py``).

Nothing here talks to FL Studio or to a MIDI port: a fake host stands in for FL Studio's ``general``/``mixer``/
``transport``/``ui`` modules (values replayed from the live trials of 2026-11-18) and a fake transport stands in for
the MIDI ports. The fake fader curve (``fake_db``) is a straight line through the two live points (0.80 -> 0 dB,
0.78 -> -0.574523 dB). It is a test stand-in only, not a claim about FL Studio's normalized-to-dB mapping.
"""

import dataclasses
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "fl_studio"
sys.path.insert(0, str(ROOT))  # device_bridge imports bridge_protocol by name, as inside FL Studio


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


protocol = _load("bridge_protocol")
sys.modules["bridge_protocol"] = protocol
device = _load("device_bridge")
client_module = _load("bridge_client")

TITLE = "Thesis Bass Bridge Test"
CMD_ID = "0123456789abcdef"
STRAY_ID = "ffffffffffffffff"
START = (0.78, -0.574523)  # live state of insert 2 "Bass" after the gain trial
TARGET = 0.76  # a normalized cut of 0.02


def fake_db(normalized):
    return (normalized - 0.8) * (0.574523 / 0.02)


# --- fakes ------------------------------------------------------------------------------------------------------------


class _General:
    def __init__(self, title, api):
        self.title, self.api, self.safe = title, api, True

    def getProjectTitle(self):
        return self.title

    def getVersion(self):
        return self.api

    def safeToEdit(self):
        return self.safe


class _Mixer:
    def __init__(self, names, volumes, curve=None):
        self.names, self.volumes, self.writes, self.curve = names, volumes, [], curve or fake_db

    def trackCount(self):
        return len(self.names) + 2  # Master (0) and the trailing "Current" track

    def getTrackName(self, index):
        return self.names[index - 1]

    def getTrackVolume(self, index, mode=0):
        return self.volumes[index - 1][mode]

    def setTrackVolume(self, index, value):
        self.writes.append((index, value))
        self.volumes[index - 1] = (value, self.curve(value))


class _Transport:
    def __init__(self):
        self.playing = False

    def isPlaying(self):
        return self.playing


class _UI:
    def getVersion(self, mode=0):
        return "Producer Edition v26.1.7 [build 5654]"


class FakeHost:
    def __init__(self, title=TITLE, api=45, names=("Kick", "Bass"), volumes=((0.8, 0.0), START), curve=None):
        self.general, self.mixer = _General(title, api), _Mixer(list(names), list(volumes), curve)
        self.transport, self.ui = _Transport(), _UI()


class FakeTransport:
    """Records sent bytes; ``receive`` returns queued replies in order, then None (no reply) until the deadline."""

    def __init__(self, replies=(), responder=None):
        self.sent, self.replies, self.responder = [], list(replies), responder

    def send(self, raw):
        self.sent.append(raw)
        if self.responder is not None:
            answer = self.responder(raw)
            if answer is not None:
                self.replies.append(answer)

    def receive(self, timeout_s):
        return self.replies.pop(0) if self.replies else None


class FakeClock:
    def __init__(self, step=0.25):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


class Script:
    """One script session: a host and the in-memory duplicate history (lost on reload: make a new Script)."""

    def __init__(self, host=None):
        self.host, self.history = host or FakeHost(), {}

    def __call__(self, raw):
        return device.handle(raw, self.host, self.history)

    def ask(self, message):
        return protocol.decode(self(protocol.encode(message)))


def _command(**changes):
    message = protocol.inspect_command(CMD_ID, TITLE, 2, "Bass")
    message.update(changes)
    return message


def _write(command_id_=CMD_ID, expected=START, target=TARGET, title=TITLE, index=2, name="Bass"):
    return protocol.set_gain_command(command_id_, title, index, name, expected[0], expected[1], target)


def _raw_write(**changes):
    """A set_gain command dict built without validation (for refusal tests of the validator itself)."""
    message = {"v": 1, "id": CMD_ID, "type": "set_gain", "project_title": TITLE, "insert": {"index": 2, "name": "Bass"},
               "expected": {"normalized": START[0], "db": START[1]}, "target_normalized": TARGET}  # fmt: skip
    message.update(changes)
    return message


def _ask(message, host=None):
    return Script(host).ask(message)


def _raw(payload: bytes) -> bytes:
    return bytes([0xF0, 0x7D]) + payload + bytes([0xF7])


def _accepted(command, answer):
    """The client's acceptance test: structure and consistency with the command."""
    protocol.validate_reply(answer)
    assert protocol.reply_consistency(command, answer) is None
    return answer


# --- wire protocol ----------------------------------------------------------------------------------------------------


def test_encode_is_compact_sorted_ascii_sysex_and_round_trips():
    raw = protocol.encode(_command(project_title="Café"))
    assert raw[:2] == b"\xf0\x7d" and raw[-1] == 0xF7
    assert all(0x20 <= b <= 0x7E for b in raw[2:-1])
    assert b'"id":"0123456789abcdef"' in raw and b", " not in raw
    assert protocol.decode(raw)["project_title"] == "Café"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"\xf0\x7d{}", "framing"),  # no F7
        (b"\x7d{}\xf7", "framing"),  # no F0
        (b"\xf0\x7d\xf7", "framing"),  # empty payload
        (b"\xf0\x41{}\xf7", "manufacturer"),
        (_raw(b'{"a":"\x80"}'), "encoding"),  # 8-bit byte
        (_raw(b'{"a":\n1}'), "encoding"),  # control character
        (_raw(b"{not json}"), "json"),
        (_raw(b"[1,2]"), "json"),
        (_raw(b'{"a":NaN}'), "json"),
        (_raw(b'{"a":"' + b"x" * 1100 + b'"}'), "too_large"),
    ],
)
def test_decode_rejects_malformed_messages(raw, code):
    with pytest.raises(protocol.ProtocolError) as info:
        protocol.decode(raw)
    assert info.value.code == code


def test_encode_rejects_messages_over_the_size_limit_and_non_finite_numbers():
    with pytest.raises(protocol.ProtocolError) as info:
        protocol.encode({"pad": "x" * protocol.MAX_MESSAGE_BYTES})
    assert info.value.code == "too_large"
    with pytest.raises(protocol.ProtocolError) as info:
        protocol.encode({"a": float("inf")})
    assert info.value.code == "json"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"v": 2}, "version"),
        ({"v": True}, "version"),
        ({"id": "short"}, "field"),
        ({"id": "has space in it"}, "field"),
        ({"type": "delete"}, "type"),
        ({"project_title": ""}, "field"),
        ({"project_title": 7}, "field"),
        ({"insert": {"index": 2}}, "field"),
        ({"insert": {"index": True, "name": "Bass"}}, "field"),
        ({"insert": {"index": 0, "name": "Bass"}}, "field"),
        ({"insert": {"index": 2, "name": "Bass", "extra": 1}}, "field"),
        ({"target_normalized": 0.7}, "field"),  # write field on an inspect command
        ({"expected": {"normalized": 0.78, "db": -0.57}}, "field"),
        ({"extra": 1}, "field"),
    ],
)
def test_validate_command_rejects_wrong_or_unknown_fields(changes, code):
    with pytest.raises(protocol.ProtocolError) as info:
        protocol.validate_command(_command(**changes))
    assert info.value.code == code


def test_validate_command_rejects_missing_fields():
    message = _command()
    del message["project_title"]
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(message)


def test_text_limits_count_escaped_bytes_not_characters():
    assert protocol.validate_command(_command(project_title="é" * 21))  # 21 x 6 = 126 bytes
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(_command(project_title="é" * 22))  # 132 bytes
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(_command(project_title="x" * (protocol.MAX_TITLE_BYTES + 1)))
    assert protocol.validate_command(_command(insert={"index": 2, "name": "n" * protocol.MAX_NAME_BYTES}))
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(_command(insert={"index": 2, "name": "é" * 11}))  # 66 bytes


# --- set_gain command validation --------------------------------------------------------------------------------------


def test_set_gain_command_is_built_and_validated():
    command = _write()
    assert command == _raw_write()
    assert protocol.decode(protocol.encode(command)) == command


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"expected": {"normalized": 0.78}}, "field"),  # dB missing
        ({"expected": {"normalized": 0.78, "db": -0.57, "x": 1}}, "field"),
        ({"expected": {"normalized": True, "db": -0.57}}, "field"),
        ({"expected": {"normalized": 0.78, "db": "x"}}, "field"),
        ({"expected": {"normalized": float("nan"), "db": -0.57}}, "field"),
        ({"expected": {"normalized": 0.78, "db": float("-inf")}}, "field"),
        ({"expected": {"normalized": 1.2, "db": 0.0}}, "field"),
        ({"expected": None}, "field"),
        ({"target_normalized": None}, "field"),
        ({"target_normalized": True}, "field"),
        ({"target_normalized": 0.0}, "bounds"),  # target must be > 0
        ({"expected": {"normalized": 0.02, "db": -40.0}, "target_normalized": 0.0}, "bounds"),
        ({"target_normalized": 0.778}, "bounds"),  # cut 0.002 is not > 0.002
        ({"target_normalized": 0.779}, "bounds"),  # cut 0.001
        ({"target_normalized": 0.749}, "bounds"),  # cut 0.031 > 0.03
        ({"target_normalized": 0.79}, "bounds"),  # a raise, not a cut
        ({"target_normalized": 0.78}, "bounds"),  # no change
    ],
)
def test_set_gain_command_refuses_bad_fields_and_out_of_bound_cuts(changes, code):
    with pytest.raises(protocol.ProtocolError) as info:
        protocol.validate_command(_raw_write(**changes))
    assert info.value.code == code


def test_set_gain_missing_target_or_expected_is_refused():
    for key in ("expected", "target_normalized"):
        message = _raw_write()
        del message[key]
        with pytest.raises(protocol.ProtocolError):
            protocol.validate_command(message)


@pytest.mark.parametrize("target", [0.7779, 0.75])  # cut 0.0021 and exactly 0.03 (both edges, decimal resolution)
def test_set_gain_accepts_cuts_at_the_edges(target):
    assert protocol.validate_command(_raw_write(target_normalized=target))


# --- size budget --------------------------------------------------------------------------------------------------------

LONG_NUMBER = -1.2345678901234567e-05  # one of the longest float reprs


def _max_identity():
    return {"title": "é" * (protocol.MAX_TITLE_BYTES // 6), "name": "é" * (protocol.MAX_NAME_BYTES // 6)}


def test_worst_case_commands_and_replies_fit_the_size_limit():
    ident = _max_identity()
    long_id = "x" * 64
    command = protocol.set_gain_command(long_id, ident["title"], 2**31 - 1, ident["name"], 0.7812345678901234,
                                        LONG_NUMBER, 0.7612345678901234)  # fmt: skip
    gain = {"normalized": 0.7812345678901234, "db": LONG_NUMBER}
    write_obs = {"project_title": ident["title"], "fl_version": "v" * protocol.MAX_VERSION_BYTES, "api": 2**31 - 1,
                 "insert": {"index": 2**31 - 1, "name": ident["name"]}, "before": gain, "after": gain,
                 "db_change": LONG_NUMBER, "replayed": False}  # fmt: skip
    inspect_obs = {k: v for k, v in write_obs.items() if k not in ("before", "after", "db_change", "replayed")}
    replies = [
        protocol.reply(long_id, "applied", write_obs),
        protocol.reply(long_id, "uncertain", write_obs, "e" * protocol.MAX_ERROR_BYTES),
        protocol.reply(long_id, "ok", {**inspect_obs, "gain": gain}),
    ]
    sizes = [len(protocol.encode(m)) for m in (command, *replies)]
    assert max(sizes) <= protocol.MAX_MESSAGE_BYTES
    for message in replies:
        protocol.validate_reply(message)


def test_script_clips_long_observed_text_so_a_mismatch_reply_still_fits():
    host = FakeHost(title="é" * 400, names=("Kick", "é" * 300))
    for command in (_command(), _write()):
        answer = _ask(command, host)
        assert answer["status"] == "identity_mismatch" and answer["error"] == "project_title"
        assert len(protocol.encode(answer)) <= protocol.MAX_MESSAGE_BYTES
        assert len(json.dumps(answer["observed"]["project_title"])) - 2 <= protocol.MAX_TITLE_BYTES


# --- reply validation ---------------------------------------------------------------------------------------------------


def _observed(title=TITLE, index=2, name="Bass", gain=START, api=45):
    return {
        "project_title": title,
        "fl_version": "Producer Edition v26.1.7 [build 5654]",
        "api": api,
        "insert": {"index": index, "name": name},
        "gain": None if gain is None else {"normalized": gain[0], "db": gain[1]},
    }


def _write_observed(title=TITLE, index=2, name="Bass", before=START, after=(TARGET, fake_db(TARGET)), replayed=False):
    obs = _observed(title, index, name, gain=None)
    del obs["gain"]
    obs["before"] = None if before is None else {"normalized": before[0], "db": before[1]}
    obs["after"] = None if after is None else {"normalized": after[0], "db": after[1]}
    obs["db_change"] = None if before is None or after is None else after[1] - before[1]
    obs["replayed"] = replayed
    return obs


def _ok(command_id_=CMD_ID, **observed):
    return protocol.reply(command_id_, "ok", _observed(**observed))


def _applied(command_id_=CMD_ID, **observed):
    return protocol.reply(command_id_, "applied", _write_observed(**observed))


def test_validate_reply_accepts_well_formed_replies_of_every_status():
    for good in (
        _ok(),
        protocol.reply(CMD_ID, "identity_mismatch", _observed(title="Other", gain=None), "project_title"),
        protocol.reply(CMD_ID, "identity_mismatch", _observed(index=99, name=None, gain=None), "insert"),
        protocol.reply(None, "invalid_command", error="json"),
        protocol.reply(CMD_ID, "unsupported", error="x"),
        protocol.reply(CMD_ID, "error", error="RuntimeError"),
        _applied(),
        protocol.reply(CMD_ID, "precondition_failed", _write_observed(after=None), "start_gain"),
        protocol.reply(CMD_ID, "precondition_failed", _write_observed(before=None, after=None), "transport_playing"),
        protocol.reply(CMD_ID, "uncertain", _write_observed(after=None), "exception_after_write:RuntimeError"),
        protocol.reply(CMD_ID, "uncertain", _write_observed(), "readback_disagrees"),
        protocol.reply(
            CMD_ID, "identity_mismatch", _write_observed(title="X", before=None, after=None), "project_title"
        ),
    ):
        assert protocol.validate_reply(good) is good


def _with_gain(**gain):
    message = _ok()
    message["observed"]["gain"].update(gain)
    return message


def _without(key):
    message = _ok()
    del message["observed"][key]
    return message


def _applied_with(**changes):
    message = _applied()
    message["observed"].update(changes)
    return message


@pytest.mark.parametrize(
    "bad",
    [
        _ok(gain=None),  # ok without gain
        _with_gain(normalized=True),  # bool is not a number
        _with_gain(db=False),
        _with_gain(normalized="0.78"),
        _with_gain(normalized=float("nan")),
        _with_gain(db=float("inf")),
        _with_gain(normalized=1.2),  # outside the documented fader range
        _with_gain(normalized=-0.1),
        _with_gain(extra=1),
        _without("project_title"),
        _without("api"),
        {**_ok(), "observed": {**_ok()["observed"], "extra": 1}},
        _ok(name=None),  # ok must name the insert
        _ok(index=True),
        _ok(api=True),
        _ok(api="45"),
        _ok(title=None),
        _ok(command_id_=None),  # ok must carry the command ID
        _ok(command_id_="bad id!"),
        {**_ok(), "error": "x"},  # ok carries no error
        {**_ok(), "extra": 1},
        {**_ok(), "status": "done"},
        {**_ok(), "type": "inspect"},
        protocol.reply(CMD_ID, "identity_mismatch", None, "insert"),
        protocol.reply(CMD_ID, "identity_mismatch", _observed(title="Other"), "project_title"),  # gain leaked
        protocol.reply(CMD_ID, "unsupported", _observed(), "set_gain"),  # observations on a non-identity status
        protocol.reply(CMD_ID, "error"),  # error without a reason
        # write-shaped replies
        protocol.reply(CMD_ID, "ok", _write_observed()),  # ok needs the inspect shape
        protocol.reply(CMD_ID, "applied", _observed()),  # applied needs the write shape
        _applied_with(after=None),
        _applied_with(before=None),
        _applied_with(db_change=None),
        _applied_with(db_change=float("nan")),
        _applied_with(replayed=1),
        _applied_with(gain=None),  # mixed shapes
        _applied_with(after={"normalized": 0.76}),
        {**_applied(), "error": "x"},
        protocol.reply(CMD_ID, "precondition_failed", _write_observed(), "start_gain"),  # after on a refusal
        protocol.reply(CMD_ID, "precondition_failed", None, "start_gain"),
        protocol.reply(CMD_ID, "uncertain", None, "x"),
        protocol.reply(CMD_ID, "uncertain", _write_observed()),  # uncertain without reason
        protocol.reply(CMD_ID, "identity_mismatch", _write_observed(title="X"), "project_title"),  # gain leaked
    ],
)
def test_validate_reply_rejects_incomplete_or_ill_typed_replies(bad):
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_reply(bad)


def test_reply_consistency_checks_identity_against_the_command():
    command = _command()
    assert protocol.reply_consistency(command, _ok()) is None
    assert protocol.reply_consistency(command, _ok(title="Other")) == "ok_identity_differs"
    assert protocol.reply_consistency(command, _ok(index=1, name="Bass")) == "ok_identity_differs"
    assert protocol.reply_consistency(command, _ok(name="Kick")) == "ok_identity_differs"
    equal = protocol.reply(CMD_ID, "identity_mismatch", _observed(gain=None), "insert")
    assert protocol.reply_consistency(command, equal) == "mismatch_identity_equal"
    real = protocol.reply(CMD_ID, "identity_mismatch", _observed(title="Other", gain=None), "project_title")
    assert protocol.reply_consistency(command, real) is None


def test_reply_consistency_for_writes():
    command = _write()
    assert protocol.reply_consistency(command, _applied()) is None
    assert protocol.reply_consistency(command, _ok()) == "status_not_for_type"
    assert protocol.reply_consistency(_command(), _applied()) == "status_not_for_type"
    mismatch_inspect_shape = protocol.reply(CMD_ID, "identity_mismatch", _observed(title="X", gain=None), "x")
    assert protocol.reply_consistency(command, mismatch_inspect_shape) == "shape_differs"
    assert protocol.reply_consistency(command, _applied(title="Other")) == "write_identity_differs"
    refusal = protocol.reply(CMD_ID, "precondition_failed", _write_observed(name="Kick", after=None), "start_gain")
    assert protocol.reply_consistency(command, refusal) == "write_identity_differs"
    # applied values that contradict the command or each other
    assert protocol.reply_consistency(command, _applied(after=(0.77, fake_db(0.77)))) == "applied_values_inconsistent"
    assert protocol.reply_consistency(command, _applied(before=(0.7815, START[1]))) == "applied_values_inconsistent"
    assert protocol.reply_consistency(command, _applied(before=(0.78, -0.7))) == "applied_values_inconsistent"
    assert protocol.reply_consistency(command, _applied(after=(TARGET, START[1]))) == "applied_values_inconsistent"
    bad_change = _applied()
    bad_change["observed"]["db_change"] = -1.0
    assert protocol.reply_consistency(command, bad_change) == "applied_values_inconsistent"
    # an uncertain reply is consistent whatever its gain values: it reports, it does not claim success
    uncertain = protocol.reply(CMD_ID, "uncertain", _write_observed(after=(0.5, -9.0)), "readback_disagrees")
    assert protocol.reply_consistency(command, uncertain) is None


# --- FL Studio script: inspect (fake host) ----------------------------------------------------------------------------


def test_inspect_ok_reports_identity_and_both_gain_forms_without_writing():
    host = FakeHost()
    answer = _ask(_command(), host)
    assert answer["id"] == CMD_ID and answer["status"] == "ok" and answer["error"] is None
    assert answer["observed"] == _observed()
    assert host.mixer.writes == []
    _accepted(_command(), answer)


def test_project_title_mismatch_withholds_gain():
    answer = _ask(_command(), FakeHost(title="Other Project"))
    assert answer["status"] == "identity_mismatch" and answer["error"] == "project_title"
    assert answer["observed"]["project_title"] == "Other Project" and answer["observed"]["gain"] is None


def test_insert_name_mismatch_withholds_gain():
    answer = _ask(_command(insert={"index": 1, "name": "Bass"}))
    assert answer["status"] == "identity_mismatch" and answer["error"] == "insert"
    assert answer["observed"]["insert"] == {"index": 1, "name": "Kick"} and answer["observed"]["gain"] is None


@pytest.mark.parametrize("index", [3, 99])  # 3 is the "Current" track of a two-insert fake, 99 does not exist
def test_insert_out_of_range_is_an_identity_mismatch(index):
    answer = _ask(_command(insert={"index": index, "name": "Bass"}))
    assert answer["status"] == "identity_mismatch" and answer["observed"]["insert"]["name"] is None


def test_invalid_command_echoes_a_well_formed_id_and_none_otherwise():
    answer = _ask(_command(v=2))
    assert answer["status"] == "invalid_command" and answer["id"] == CMD_ID and answer["error"] == "version"
    answer = protocol.decode(Script()(_raw(b"{oops}")))
    assert answer["status"] == "invalid_command" and answer["id"] is None and answer["error"] == "json"
    answer = _ask(_command(id="bad id!"))
    assert answer["status"] == "invalid_command" and answer["id"] is None


def test_foreign_sysex_is_left_unhandled():
    assert Script()(b"\xf0\x41\x10\x42\xf7") is None


def test_old_api_is_reported_as_error():
    answer = _ask(_command(), FakeHost(api=37))
    assert answer["status"] == "error" and answer["error"] == "api_too_old"


def test_host_exception_becomes_an_error_reply():
    host = FakeHost()
    host.mixer.getTrackVolume = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    answer = _ask(_command(), host)
    assert answer["status"] == "error" and answer["error"] == "RuntimeError" and answer["id"] == CMD_ID


def test_script_reports_error_instead_of_sending_an_invalid_ok():
    host = FakeHost(volumes=((0.8, 0.0), (float("nan"), float("nan"))))
    answer = _ask(_command(), host)
    assert answer["status"] == "error" and answer["error"] == "invalid_observation" and answer["observed"] is None


def test_inspect_with_a_reused_inspect_id_reads_again():
    script = Script()
    first = script.ask(_command())
    script.host.mixer.volumes[1] = (0.77, fake_db(0.77))
    second = script.ask(_command())
    assert first["observed"]["gain"]["normalized"] == 0.78 and second["observed"]["gain"]["normalized"] == 0.77


# --- FL Studio script: set_gain ---------------------------------------------------------------------------------------


def test_set_gain_applies_once_and_reports_before_after_and_db_change():
    script = Script()
    answer = _accepted(_write(), script.ask(_write()))
    assert answer["status"] == "applied" and answer["error"] is None
    assert script.host.mixer.writes == [(2, TARGET)]
    obs = answer["observed"]
    assert obs["before"] == {"normalized": START[0], "db": START[1]}
    assert obs["after"] == {"normalized": TARGET, "db": fake_db(TARGET)}
    assert obs["db_change"] == pytest.approx(fake_db(TARGET) - START[1])
    assert obs["replayed"] is False


def _refusal(host, command=None):
    script = Script(host)
    answer = script.ask(command or _write())
    assert host.mixer.writes == [], "a refusal must not write"
    _accepted(command or _write(), answer)
    return answer


def test_set_gain_refuses_old_api_without_writing():
    host = FakeHost(api=37)
    answer = Script(host).ask(_write())
    assert answer["status"] == "error" and answer["error"] == "api_too_old" and host.mixer.writes == []


def test_set_gain_refuses_project_title_mismatch():
    answer = _refusal(FakeHost(title="Other Project"))
    assert answer["status"] == "identity_mismatch" and answer["error"] == "project_title"
    assert answer["observed"]["before"] is None and answer["observed"]["after"] is None


def test_set_gain_refuses_insert_name_mismatch():
    answer = _refusal(FakeHost(names=("Kick", "Sub")))
    assert answer["status"] == "identity_mismatch" and answer["error"] == "insert"


def test_set_gain_refuses_out_of_range_insert():
    command = _write(index=5)
    answer = _refusal(FakeHost(), command)
    assert answer["status"] == "identity_mismatch" and answer["observed"]["insert"]["name"] is None


def test_set_gain_refuses_while_transport_plays():
    host = FakeHost()
    host.transport.playing = True
    answer = _refusal(host)
    assert answer["status"] == "precondition_failed" and answer["error"] == "transport_playing"


def test_set_gain_refuses_when_editing_is_unavailable():
    host = FakeHost()
    host.general.safe = False
    answer = _refusal(host)
    assert answer["status"] == "precondition_failed" and answer["error"] == "edit_unavailable"


@pytest.mark.parametrize(
    "current",
    [
        (0.7815, START[1]),  # normalized off by 0.0015 > 0.001
        (0.7785, START[1]),
        (0.78, START[1] - 0.06),  # dB off by 0.06 > 0.05
        (0.78, START[1] + 0.06),
    ],
)
def test_set_gain_refuses_a_starting_gain_outside_the_trial_tolerances(current):
    answer = _refusal(FakeHost(volumes=((0.8, 0.0), current)))
    assert answer["status"] == "precondition_failed" and answer["error"] == "start_gain"
    assert answer["observed"]["before"] == {"normalized": current[0], "db": current[1]}


def test_set_gain_accepts_a_starting_gain_within_the_tolerances():
    script = Script(FakeHost(volumes=((0.8, 0.0), (0.7809, START[1] + 0.04))))
    answer = _accepted(_write(), script.ask(_write()))
    assert answer["status"] == "applied" and script.host.mixer.writes == [(2, TARGET)]


def test_set_gain_refuses_a_cut_outside_the_bounds_measured_from_the_actual_start():
    # expected 0.78 -> target 0.7779 is a valid 0.0021 cut, but the fader actually sits at 0.7799 (within the 0.001
    # start tolerance), so the cut that would be applied is 0.002, which is not > 0.002
    host = FakeHost(volumes=((0.8, 0.0), (0.7799, START[1])))
    command = _write(target=0.7779)
    answer = _refusal(host, command)
    assert answer["status"] == "precondition_failed" and answer["error"] == "cut_bounds"


def test_set_gain_exception_before_the_setter_is_an_error_without_write():
    host = FakeHost()
    host.mixer.getTrackVolume = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    answer = Script(host).ask(_write())
    assert answer["status"] == "error" and answer["error"] == "RuntimeError" and host.mixer.writes == []


def test_set_gain_read_back_disagreement_is_uncertain_and_reports_what_was_read():
    host = FakeHost()
    original = host.mixer.setTrackVolume

    def sticky(index, value):  # the fader moves somewhere else than requested
        original(index, value)
        host.mixer.volumes[index - 1] = (0.77, fake_db(0.77))

    host.mixer.setTrackVolume = sticky
    answer = _accepted(_write(), Script(host).ask(_write()))
    assert answer["status"] == "uncertain" and answer["error"] == "readback_disagrees"
    assert answer["observed"]["after"] == {"normalized": 0.77, "db": fake_db(0.77)}
    assert host.mixer.writes == [(2, TARGET)]


def test_set_gain_without_a_db_decrease_is_uncertain():
    host = FakeHost()
    original = host.mixer.setTrackVolume

    def no_db_change(index, value):
        original(index, value)
        host.mixer.volumes[index - 1] = (value, START[1])  # fader moved but dB read-back did not

    host.mixer.setTrackVolume = no_db_change
    answer = Script(host).ask(_write())
    assert answer["status"] == "uncertain" and answer["error"] == "readback_disagrees"


def test_set_gain_exception_in_the_setter_is_uncertain():
    host = FakeHost()
    calls = []

    def failing(index, value):
        calls.append((index, value))
        raise RuntimeError("setter failed after it may have written")

    host.mixer.setTrackVolume = failing
    answer = _accepted(_write(), Script(host).ask(_write()))
    assert answer["status"] == "uncertain" and answer["error"] == "exception_after_write:RuntimeError"
    assert answer["observed"]["before"]["normalized"] == START[0] and answer["observed"]["after"] is None
    assert calls == [(2, TARGET)]


def test_set_gain_exception_in_the_read_back_is_uncertain():
    host = FakeHost()
    reads = []
    original_read = host.mixer.getTrackVolume

    def read(index, mode=0):
        reads.append(index)
        if host.mixer.writes:
            raise OSError("read-back failed")
        return original_read(index, mode)

    host.mixer.getTrackVolume = read
    answer = Script(host).ask(_write())
    assert answer["status"] == "uncertain" and answer["error"] == "exception_after_write:OSError"
    assert host.mixer.writes == [(2, TARGET)]


def test_set_gain_invalid_read_back_is_uncertain_not_error():
    host = FakeHost()
    original = host.mixer.setTrackVolume

    def nan_after(index, value):
        original(index, value)
        host.mixer.volumes[index - 1] = (float("nan"), float("nan"))

    host.mixer.setTrackVolume = nan_after
    answer = _accepted(_write(), Script(host).ask(_write()))
    assert answer["status"] == "uncertain" and answer["error"] == "invalid_observation"
    assert host.mixer.writes == [(2, TARGET)]


@pytest.mark.parametrize(
    "after",
    [
        (None, -1.0),  # read-back returns nothing: after["db"] - before["db"] would raise TypeError
        (TARGET, None),
        ("0.76", "-1.1"),  # strings: arithmetic raises TypeError
        (TARGET, object()),
    ],
)
def test_any_failure_while_processing_a_read_back_is_uncertain_not_error(after):
    host = FakeHost()
    original = host.mixer.setTrackVolume

    def odd_read_back(index, value):
        original(index, value)
        host.mixer.volumes[index - 1] = after

    host.mixer.setTrackVolume = odd_read_back
    script = Script(host)
    answer = _accepted(_write(), script.ask(_write()))
    assert answer["status"] == "uncertain" and host.mixer.writes == [(2, TARGET)]
    assert answer["observed"]["project_title"] == TITLE and answer["observed"]["insert"] == {"index": 2, "name": "Bass"}
    assert script.ask(_write())["status"] == "uncertain"  # recorded and replayed, never rewritten
    assert host.mixer.writes == [(2, TARGET)]


@pytest.mark.parametrize("stage", ["readback_agrees", "_finish", "encode"])
def test_failure_in_post_write_bookkeeping_is_uncertain(monkeypatch, stage):
    host = FakeHost()
    script = Script(host)
    if stage == "readback_agrees":
        monkeypatch.setattr(protocol, "readback_agrees", lambda *a: (_ for _ in ()).throw(ArithmeticError("x")))
    elif stage == "_finish":
        monkeypatch.setattr(device, "_finish", lambda *a: (_ for _ in ()).throw(KeyError("x")))
    else:
        real_encode = protocol.encode
        calls = []

        def flaky_encode(message):  # the first encoding of an applied reply fails, as if it were too large
            calls.append(message.get("status"))
            if message.get("status") == "applied" and calls.count("applied") == 1:
                raise protocol.ProtocolError("too_large", "simulated")
            return real_encode(message)

        monkeypatch.setattr(protocol, "encode", flaky_encode)
    answer = protocol.decode(script(protocol.encode(_write())))
    assert answer["status"] == "uncertain" and answer["id"] == CMD_ID
    assert host.mixer.writes == [(2, TARGET)]
    protocol.validate_reply(answer)


def test_post_write_fallback_survives_unusable_identity_values():
    host = FakeHost()
    host.ui.getVersion = lambda mode=0: float("nan")  # not text: the reply's own check fails after the write
    host.general.getVersion = lambda: 45.0  # a float API number passes the < 38 check but is not an integer
    answer = _accepted(_write(), Script(host).ask(_write()))
    assert answer["status"] == "uncertain" and answer["error"] == "invalid_observation"
    assert answer["observed"]["api"] == device.MIN_API_VERSION and host.mixer.writes == [(2, TARGET)]


# --- duplicates -------------------------------------------------------------------------------------------------------


def test_duplicate_id_with_the_same_payload_returns_the_recorded_result_without_writing():
    script = Script()
    first = script.ask(_write())
    second = script.ask(_write())
    assert script.host.mixer.writes == [(2, TARGET)]
    assert second["status"] == "applied" and second["observed"]["replayed"] is True
    assert {**second["observed"], "replayed": False} == first["observed"]
    _accepted(_write(), second)


def test_duplicate_of_a_refusal_replays_the_refusal():
    host = FakeHost()
    host.transport.playing = True
    script = Script(host)
    first = script.ask(_write())
    host.transport.playing = False  # the session changed, but the same command keeps its recorded result
    second = script.ask(_write())
    assert first["status"] == second["status"] == "precondition_failed" and second["observed"]["replayed"] is True
    assert host.mixer.writes == []


def test_duplicate_of_an_uncertain_result_does_not_write_again():
    host = FakeHost()
    host.mixer.setTrackVolume = lambda i, v: (_ for _ in ()).throw(RuntimeError("x"))
    script = Script(host)
    assert script.ask(_write())["status"] == "uncertain"
    host.mixer.setTrackVolume = lambda i, v: pytest.fail("a replay must not call the setter")
    assert script.ask(_write())["status"] == "uncertain"


@pytest.mark.parametrize(
    "conflict",
    [
        _write(target=0.77),
        _write(expected=(0.78, -0.6)),
        _write(name="Kick", index=1),
        _command(),  # an inspect reusing a write's ID
    ],
)
def test_reused_id_with_different_content_is_rejected_without_writing(conflict):
    script = Script()
    script.ask(_write())
    answer = script.ask(conflict)
    assert answer["status"] == "invalid_command" and answer["error"] == "duplicate_id_conflict"
    assert answer["id"] == CMD_ID and script.host.mixer.writes == [(2, TARGET)]


def test_write_reusing_an_inspect_id_with_different_content_is_rejected():
    script = Script()
    script.ask(_command())
    answer = script.ask(_write())
    assert answer["status"] == "invalid_command" and answer["error"] == "duplicate_id_conflict"
    assert script.host.mixer.writes == []


def test_after_reload_the_history_is_lost_and_only_the_start_gain_precondition_blocks_a_resend():
    host = FakeHost()
    assert Script(host).ask(_write())["status"] == "applied"
    reloaded = Script(host)  # same FL Studio session, script reloaded: empty duplicate history
    answer = reloaded.ask(_write())
    assert answer["status"] == "precondition_failed" and answer["error"] == "start_gain"
    assert answer["observed"]["replayed"] is False
    assert host.mixer.writes == [(2, TARGET)]


# --- client: correlation, validation, write guard (fake transport) ----------------------------------------------------


def _client(script=None, replies=(), step=0.01):
    transport = FakeTransport(replies, responder=script)
    return client_module.BridgeClient(transport, FakeClock(step)), transport


def test_client_round_trip_through_the_simulated_script():
    client, transport = _client(Script())
    outcome = client.inspect(TITLE, 2, "Bass", command_id=CMD_ID)
    assert outcome.status == "replied" and outcome.reply["status"] == "ok"
    assert outcome.reply["id"] == outcome.command["id"] == CMD_ID
    assert len(transport.sent) == 1 and protocol.decode(transport.sent[0]) == outcome.command


def test_client_set_gain_round_trip():
    script = Script()
    client, transport = _client(script)
    outcome = client.set_gain(TITLE, 2, "Bass", START[0], START[1], TARGET, command_id=CMD_ID)
    assert outcome.status == "replied" and outcome.reply["status"] == "applied"
    assert protocol.decode(transport.sent[0]) == _write()
    assert script.host.mixer.writes == [(2, TARGET)] and client.write_block is None


def test_client_refuses_an_out_of_bounds_write_locally():
    client, transport = _client(Script())
    with pytest.raises(protocol.ProtocolError) as info:
        client.set_gain(TITLE, 2, "Bass", START[0], START[1], 0.70)
    assert info.value.code == "bounds" and transport.sent == []


def test_client_generates_unique_ids():
    client, _ = _client(step=1.0)
    ids = {client.inspect(TITLE, 2, "Bass", timeout_s=0.5).command["id"] for _ in range(20)}
    assert len(ids) == 20 and all(protocol.command_id({"id": i}) for i in ids)


def test_client_ignores_stray_and_malformed_replies_and_matches_by_id():
    stray = protocol.encode(_ok(STRAY_ID, gain=(0.5, -6.0)))
    invalid_stray = protocol.encode(_ok(STRAY_ID, api=True))  # invalid, but not for this command
    stray_write = protocol.encode(_applied(STRAY_ID))
    mine = protocol.encode(_ok())
    replies = [b"\xf0\x41\x00\xf7", _raw(b"{bad"), stray, invalid_stray, stray_write, mine]
    client, _ = _client(replies=replies)
    outcome = client.inspect(TITLE, 2, "Bass", command_id=CMD_ID)
    assert outcome.status == "replied" and outcome.reply["observed"]["gain"]["normalized"] == 0.78
    assert [s["id"] for s in outcome.stray_replies] == [STRAY_ID, STRAY_ID]
    assert [m["error"] for m in outcome.malformed_replies] == ["json", "field"]


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        (_ok(gain=None), "field"),  # carries this command's ID but fails structure
        (_ok(api=True), "field"),
        (_ok(title="Other Project"), "ok_identity_differs"),
        (_ok(index=1, name="Kick"), "ok_identity_differs"),
        (protocol.reply(CMD_ID, "identity_mismatch", _observed(gain=None), "insert"), "mismatch_identity_equal"),
        (_applied(), "status_not_for_type"),
    ],
)
def test_client_rejects_an_invalid_reply_to_its_own_command_without_accepting_it(bad, problem):
    later_ok = protocol.encode(_ok())
    client, transport = _client(replies=[protocol.encode(bad), later_ok])
    outcome = client.inspect(TITLE, 2, "Bass", command_id=CMD_ID)
    assert outcome.status == "invalid_reply" and outcome.reply is None
    assert transport.replies == [later_ok]  # stopped at the invalid reply, did not wait for or accept another
    assert [m["error"] for m in outcome.malformed_replies] == [problem]


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        (_applied(after=(0.77, fake_db(0.77))), "applied_values_inconsistent"),
        (_applied(title="Other"), "write_identity_differs"),
        (_ok(), "status_not_for_type"),
        (_applied_with(after=None), "field"),
    ],
)
def test_client_rejects_an_invalid_write_reply_and_blocks_further_writes(bad, problem):
    client, _ = _client(replies=[protocol.encode(bad)])
    outcome = client.set_gain(TITLE, 2, "Bass", START[0], START[1], TARGET, command_id=CMD_ID)
    assert outcome.status == "invalid_reply" and outcome.malformed_replies[0]["error"] == problem
    assert client.write_block is not None


def test_client_does_not_accept_a_nan_gain_reply():
    nan_reply = json.dumps(_ok(gain=(float("nan"), 0.0)), separators=(",", ":"), sort_keys=True)  # NaN literal
    client, _ = _client(replies=[_raw(nan_reply.encode("ascii"))], step=0.25)
    outcome = client.inspect(TITLE, 2, "Bass", timeout_s=1.0, command_id=CMD_ID)
    # undecodable, so its ID cannot be trusted: recorded as malformed, and the command times out unanswered
    assert outcome.status == "timeout" and outcome.reply is None
    assert [m["error"] for m in outcome.malformed_replies] == ["json"]


def test_client_reports_timeout_when_no_matching_reply_arrives():
    stray = protocol.encode(_ok(STRAY_ID, gain=(0.5, -6.0)))
    client, _ = _client(replies=[stray], step=0.25)
    outcome = client.inspect(TITLE, 2, "Bass", timeout_s=2.0, command_id=CMD_ID)
    assert outcome.status == "timeout" and outcome.reply is None
    assert outcome.elapsed_s >= 2.0 and len(outcome.stray_replies) == 1
    assert client.write_block is None  # an inspect timeout changes nothing


def test_client_returns_identity_mismatch_replies_unchanged():
    client, _ = _client(Script(FakeHost(title="Other Project")))
    outcome = client.inspect(TITLE, 2, "Bass", command_id=CMD_ID)
    assert outcome.status == "replied" and outcome.reply["status"] == "identity_mismatch"


def test_write_timeout_blocks_writes_until_an_inspect_succeeds():
    script = Script()
    silent = FakeTransport()
    client = client_module.BridgeClient(silent, FakeClock(0.5))
    lost = client.set_gain(TITLE, 2, "Bass", START[0], START[1], TARGET, timeout_s=1.0)
    assert lost.status == "timeout" and client.write_block is not None
    blocked = client.set_gain(TITLE, 2, "Bass", START[0], START[1], TARGET)
    assert blocked.status == "blocked" and len(silent.sent) == 1  # nothing sent
    client.transport = FakeTransport(responder=script)
    # an inspect of another insert or with a mismatching identity does not clear the block
    assert client.inspect(TITLE, 1, "Kick").reply["status"] == "ok" and client.write_block is not None
    assert client.inspect(TITLE, 2, "Sub").reply["status"] == "identity_mismatch" and client.write_block is not None
    assert client.inspect(TITLE, 2, "Bass").reply["status"] == "ok" and client.write_block is None
    assert client.set_gain(TITLE, 2, "Bass", START[0], START[1], TARGET).reply["status"] == "applied"


def test_uncertain_reply_blocks_further_writes():
    host = FakeHost()
    host.mixer.setTrackVolume = lambda i, v: (_ for _ in ()).throw(RuntimeError("x"))
    client, _ = _client(Script(host))
    outcome = client.set_gain(TITLE, 2, "Bass", START[0], START[1], TARGET)
    assert outcome.reply["status"] == "uncertain" and client.write_block is not None
    assert client.set_gain(TITLE, 2, "Bass", START[0], START[1], TARGET).status == "blocked"


def test_refusals_do_not_block_further_writes():
    host = FakeHost()
    host.transport.playing = True
    client, _ = _client(Script(host))
    assert client.set_gain(TITLE, 2, "Bass", START[0], START[1], TARGET).reply["status"] == "precondition_failed"
    assert client.write_block is None


def test_a_write_is_journaled_as_pending_before_it_is_sent():
    script = Script()
    journal = []
    transport = FakeTransport(responder=script)
    client = client_module.BridgeClient(transport, FakeClock(0.01),
                                        journal=lambda record: journal.append((len(transport.sent), record)))  # fmt: skip
    outcome = client.set_gain(TITLE, 2, "Bass", START[0], START[1], TARGET, command_id=CMD_ID)
    assert [(sent, r["status"], r["command"]["id"]) for sent, r in journal] == [(0, "pending", CMD_ID)]
    assert outcome.reply["status"] == "applied" and client.write_block is None
    client.inspect(TITLE, 2, "Bass")
    assert len(journal) == 1  # inspects are not journaled as pending


def test_a_failed_dispatch_leaves_the_write_pending_and_blocked():
    class BrokenTransport(FakeTransport):
        def send(self, raw):
            raise OSError("midiOutLongMsg failed")

    journal = []
    client = client_module.BridgeClient(BrokenTransport(), FakeClock(0.01), journal=journal.append)
    with pytest.raises(OSError):
        client.set_gain(TITLE, 2, "Bass", START[0], START[1], TARGET, command_id=CMD_ID)
    assert client.write_block["reason"] == "pending" and client.write_block["command_id"] == CMD_ID
    assert [r["status"] for r in journal] == ["pending"]
    assert client_module.write_block_from_records(journal) == client.write_block


def test_pending_records_block_until_resolved_by_the_same_write_or_an_ok_inspect():
    write = _write()
    pending = {"status": "pending", "command": write, "reply": None}
    applied = {"status": "replied", "command": write, "reply": _applied()}
    lost = {"status": "timeout", "command": write, "reply": None}
    other = {"status": "replied", "command": _write(STRAY_ID), "reply": _applied(STRAY_ID)}
    inspect_ok = {"status": "replied", "command": _command(id=STRAY_ID), "reply": _ok(STRAY_ID)}
    conflict = {"status": "replied", "command": write,
                "reply": protocol.reply(CMD_ID, "invalid_command", error="duplicate_id_conflict")}  # fmt: skip
    blocked = {"status": "blocked", "command": write, "reply": None}
    assert client_module.write_block_from_records([pending])["reason"] == "pending"  # process died mid-write
    assert client_module.write_block_from_records([pending, applied]) is None
    assert client_module.write_block_from_records([pending, conflict]) is None  # answered: nothing written
    assert client_module.write_block_from_records([pending, lost])["reason"] == "timeout"
    assert client_module.write_block_from_records([pending, other]) is not None  # another ID does not resolve it
    assert client_module.write_block_from_records([pending, blocked]) is not None
    assert client_module.write_block_from_records([pending, inspect_ok]) is None


def test_cli_refuses_a_write_after_a_pending_record_without_an_outcome(tmp_path, capsys):
    log = tmp_path / "bridge.jsonl"
    log.write_text(json.dumps({"status": "pending", "command": _write(), "reply": None}) + "\n", encoding="utf-8")
    args = ["set-gain", "--project-title", TITLE, "--insert-index", "2", "--insert-name", "Bass",
            "--expected-normalized", "0.78", "--expected-db", "-0.574523", "--target-normalized", "0.76",
            "--log", str(log), "--out", "missing", "--in", "missing"]  # fmt: skip
    assert client_module.main(args) == 5
    assert "pending" in capsys.readouterr().err


def test_write_guard_is_rebuilt_from_the_log():
    write = _write()
    lost = {"status": "timeout", "command": write, "reply": None}
    inspect_ok = {"status": "replied", "command": _command(id=STRAY_ID), "reply": _ok(STRAY_ID)}
    assert client_module.write_block_from_records([lost]) is not None
    assert client_module.write_block_from_records([lost, inspect_ok]) is None
    assert client_module.write_block_from_records([inspect_ok, lost]) is not None
    applied = {"status": "replied", "command": write, "reply": _applied()}
    assert client_module.write_block_from_records([applied]) is None


def test_cli_set_gain_needs_a_log_and_refuses_before_opening_ports_when_blocked(tmp_path, capsys):
    args = ["set-gain", "--project-title", TITLE, "--insert-index", "2", "--insert-name", "Bass",
            "--expected-normalized", "0.78", "--expected-db", "-0.574523", "--target-normalized", "0.76"]  # fmt: skip
    with pytest.raises(SystemExit):
        client_module.main(args)  # --log is required for writes
    log = tmp_path / "bridge.jsonl"
    log.write_text(json.dumps({"status": "timeout", "command": _write(), "reply": None}) + "\n", encoding="utf-8")
    assert client_module.main([*args, "--log", str(log), "--out", "missing", "--in", "missing"]) == 5
    assert "inspect" in capsys.readouterr().err


def test_cli_rejects_an_out_of_bounds_write_before_opening_ports(tmp_path, capsys):
    args = ["set-gain", "--project-title", TITLE, "--insert-index", "2", "--insert-name", "Bass",
            "--expected-normalized", "0.78", "--expected-db", "-0.574523", "--target-normalized", "0.70",
            "--log", str(tmp_path / "bridge.jsonl"), "--out", "missing", "--in", "missing"]  # fmt: skip
    assert client_module.main(args) == 6
    assert "bounds" in capsys.readouterr().err


def test_outcome_serializes_to_json_for_the_log():
    client, _ = _client(step=1.0)
    outcome = client.inspect(TITLE, 2, "Bass", timeout_s=0.5)
    assert json.loads(json.dumps(dataclasses.asdict(outcome)))["status"] == "timeout"


# === probe_gain calibration ===========================================================================================

evaluator = _load("evaluate_calibration")
RUN = "run20261118a"
PLAN = protocol.CALIBRATION_SEQUENCE
PRIMARY = protocol.CALIBRATION_PRIMARY
WIDE = protocol.CALIBRATION_WIDE


def hypothesis_db(v):  # the design's candidate curve H, used as the fake fader law in calibration tests
    return evaluator.h_db(v)


def _probe(step=0, expected=START, target=None, command_id_=CMD_ID):
    return protocol.probe_command(command_id_, expected[0], expected[1], step, target)


def _raw_probe(**changes):
    message = {"v": 1, "id": CMD_ID, "type": "probe_gain", "project_title": TITLE,
               "insert": {"index": 2, "name": "Bass"}, "plan": protocol.CALIBRATION_PLAN_ID, "step": 0,
               "expected": {"normalized": START[0], "db": START[1]}, "target_normalized": PLAN[0]}  # fmt: skip
    message.update(changes)
    return message


# --- the pinned plan --------------------------------------------------------------------------------------------------


def test_calibration_plan_is_pinned():
    assert protocol.CALIBRATION_PLAN_ID == "fl-mixer-cal-v1"
    assert protocol.CALIBRATION_PROJECT_TITLE == TITLE
    assert protocol.CALIBRATION_INSERT == {"index": 2, "name": "Bass"}
    assert PRIMARY == (0.62, 0.65, 0.68, 0.71, 0.73, 0.75, 0.77, 0.79, 0.81, 0.83, 0.86)
    assert WIDE == (0.30, 0.45, 0.55, 0.92, 1.00)
    assert evaluator.plan_digest() == evaluator.PINNED_PLAN_DIGEST  # any change to the plan changes the digest


def test_calibration_sequence_has_the_predeclared_properties():
    assert len(PLAN) == 2 * len(PRIMARY) + len(WIDE)
    assert sorted(PLAN) == sorted(list(PRIMARY) * 2 + list(WIDE))
    assert not {0.76, 0.78, 0.80} & set(PLAN)  # the three points that selected H are not test points
    assert PLAN[0] in WIDE  # so no primary visit depends on the unknown starting fader
    assert all(a != b for a, b in zip(PLAN, PLAN[1:]))
    for point in PRIMARY:
        directions = sorted(evaluator.approach(PLAN[i - 1], point) for i, v in enumerate(PLAN) if v == point)
        assert directions == ["above", "below"], point
    assert all(0.0 < v <= 1.0 for v in PLAN)


# --- probe command validation -----------------------------------------------------------------------------------------


def test_probe_command_takes_its_target_from_the_plan():
    for step, target in enumerate(PLAN):
        command = _probe(step)
        assert command["target_normalized"] == target and command["plan"] == protocol.CALIBRATION_PLAN_ID
        assert command["project_title"] == TITLE and command["insert"] == {"index": 2, "name": "Bass"}
    restore = _probe("restore", target=0.7599999997764826)
    assert restore["step"] == "restore" and restore["target_normalized"] == 0.7599999997764826


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"plan": "other-plan"}, "plan"),
        ({"project_title": "Other Project"}, "plan"),  # probes run only on the plan's disposable project
        ({"insert": {"index": 1, "name": "Kick"}}, "plan"),
        ({"insert": {"index": 2, "name": "Sub"}}, "plan"),
        ({"step": len(PLAN)}, "plan"),
        ({"step": -1}, "plan"),
        ({"step": True}, "field"),
        ({"step": "1"}, "field"),
        ({"step": 1.0}, "field"),
        ({"target_normalized": PLAN[0] + 1e-9}, "plan"),  # not the pinned position
        ({"target_normalized": 0.5}, "plan"),
        ({"step": "restore", "target_normalized": 0.0}, "bounds"),
        ({"step": "restore", "target_normalized": 1.01}, "bounds"),
        ({"step": "restore", "target_normalized": float("nan")}, "field"),
        ({"expected": {"normalized": 0.76}}, "field"),
        ({"extra": 1}, "field"),
    ],
)
def test_probe_command_refuses_anything_off_the_plan(changes, code):
    with pytest.raises(protocol.ProtocolError) as info:
        protocol.validate_command(_raw_probe(**changes))
    assert info.value.code == code


def test_probe_command_missing_fields_are_refused():
    for key in ("plan", "step", "expected", "target_normalized"):
        message = _raw_probe()
        del message[key]
        with pytest.raises(protocol.ProtocolError):
            protocol.validate_command(message)


def test_set_gain_fields_cannot_carry_a_plan_and_probes_ignore_cut_bounds():
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command({**_write(), "plan": protocol.CALIBRATION_PLAN_ID})
    raise_probe = _probe(PLAN.index(1.0))  # a raise of +0.24 normalized: refused for set_gain, pinned for probes
    assert protocol.validate_command(raw := dict(raise_probe)) is raw


def test_worst_case_probe_messages_fit_the_size_limit():
    command = protocol.probe_command("x" * 64, 0.7812345678901234, LONG_NUMBER, "restore", 0.7612345678901234)
    gain = {"normalized": 0.7812345678901234, "db": LONG_NUMBER}
    observed = {"project_title": TITLE, "fl_version": "v" * protocol.MAX_VERSION_BYTES, "api": 2**31 - 1,
                "insert": {"index": 2, "name": "Bass"}, "before": gain, "after": gain, "db_change": LONG_NUMBER,
                "replayed": False}  # fmt: skip
    for message in (command, protocol.reply("x" * 64, "uncertain", observed, "e" * protocol.MAX_ERROR_BYTES)):
        assert len(protocol.encode(message)) <= protocol.MAX_MESSAGE_BYTES


def _probe_applied(step=0, before=START, after=None, command_id_=CMD_ID):
    after = after or (PLAN[step], fake_db(PLAN[step]))
    return protocol.reply(command_id_, "applied", _write_observed(before=before, after=after))


def test_probe_reply_consistency():
    command = _probe(0)
    assert protocol.reply_consistency(command, _probe_applied()) is None
    rise = _probe(PLAN.index(1.0))
    assert protocol.reply_consistency(rise, _probe_applied(PLAN.index(1.0))) is None  # dB may rise for a probe
    assert protocol.reply_consistency(command, _ok()) == "status_not_for_type"
    off = _probe_applied(after=(PLAN[0] + 0.01, fake_db(PLAN[0] + 0.01)))
    assert protocol.reply_consistency(command, off) == "applied_values_inconsistent"
    wrong_start = _probe_applied(before=(0.79, fake_db(0.79)))
    assert protocol.reply_consistency(command, wrong_start) == "applied_values_inconsistent"
    other = protocol.reply(CMD_ID, "applied", _write_observed(title="Other", after=(PLAN[0], fake_db(PLAN[0]))))
    assert protocol.reply_consistency(command, other) == "write_identity_differs"
    assert protocol.reply_consistency(_command(), _probe_applied()) == "status_not_for_type"


# --- probe_gain in the FL Studio script ----------------------------------------------------------------------------


def test_probe_applies_a_pinned_raise_once_and_reports_it():
    step = PLAN.index(1.0)
    script = Script()
    answer = _accepted(_probe(step), script.ask(_probe(step)))
    assert answer["status"] == "applied" and script.host.mixer.writes == [(2, 1.0)]
    assert answer["observed"]["after"]["normalized"] == 1.0 and answer["observed"]["db_change"] > 0


@pytest.mark.parametrize(
    ("setup", "status", "error"),
    [
        (lambda h: setattr(h.general, "title", "Other Project"), "identity_mismatch", "project_title"),
        (lambda h: h.mixer.names.__setitem__(1, "Sub"), "identity_mismatch", "insert"),
        (lambda h: setattr(h.transport, "playing", True), "precondition_failed", "transport_playing"),
        (lambda h: setattr(h.general, "safe", False), "precondition_failed", "edit_unavailable"),
        (lambda h: h.mixer.volumes.__setitem__(1, (0.7815, START[1])), "precondition_failed", "start_gain"),
        (lambda h: h.mixer.volumes.__setitem__(1, (0.78, START[1] - 0.06)), "precondition_failed", "start_gain"),
        (lambda h: setattr(h.general, "api", 37), "error", "api_too_old"),
    ],
)
def test_probe_keeps_every_pre_write_check(setup, status, error):
    host = FakeHost()
    setup(host)
    answer = Script(host).ask(_probe(0))
    assert (answer["status"], answer["error"]) == (status, error)
    assert host.mixer.writes == []


def test_probe_read_back_disagreement_and_post_write_failures_are_uncertain():
    host = FakeHost()
    original = host.mixer.setTrackVolume

    def elsewhere(index, value):
        original(index, value)
        host.mixer.volumes[index - 1] = (value + 0.01, fake_db(value + 0.01))

    host.mixer.setTrackVolume = elsewhere
    assert Script(host).ask(_probe(0))["error"] == "readback_disagrees"

    host = FakeHost()
    host.mixer.setTrackVolume = lambda i, v: (_ for _ in ()).throw(RuntimeError("x"))
    answer = Script(host).ask(_probe(0))
    assert answer["status"] == "uncertain" and answer["error"] == "exception_after_write:RuntimeError"

    host = FakeHost()
    original = host.mixer.setTrackVolume

    def none_db(index, value):
        original(index, value)
        host.mixer.volumes[index - 1] = (value, None)

    host.mixer.setTrackVolume = none_db
    answer = _accepted(_probe(0), Script(host).ask(_probe(0)))
    assert answer["status"] == "uncertain" and host.mixer.writes == [(2, PLAN[0])]


def test_probe_duplicate_replays_and_conflict_is_refused():
    script = Script()
    first = script.ask(_probe(0))
    assert script.ask(_probe(0))["observed"]["replayed"] is True
    conflict = script.ask(_probe(1, expected=(PLAN[0], fake_db(PLAN[0]))))  # same ID, other step
    assert conflict["error"] == "duplicate_id_conflict"
    assert first["status"] == "applied" and script.host.mixer.writes == [(2, PLAN[0])]


def test_restore_must_return_to_the_start_recorded_at_step_0_in_this_session():
    script = Script()
    script.ask(_probe(0, command_id_="cal-a-00"))
    current = (PLAN[0], fake_db(PLAN[0]))
    wrong = script.ask(_probe("restore", expected=current, target=0.77, command_id_="cal-a-r1"))  # start is 0.78
    assert wrong["status"] == "precondition_failed" and wrong["error"] == "restore_target"
    assert script.host.mixer.writes == [(2, PLAN[0])]
    exact = script.ask(_probe("restore", expected=current, target=START[0], command_id_="cal-a-r2"))
    assert exact["status"] == "applied" and script.host.mixer.volumes[1][0] == START[0]


def test_restore_after_a_reload_has_no_session_start_to_check_and_is_allowed():
    host = FakeHost(volumes=((0.8, 0.0), (PLAN[0], fake_db(PLAN[0]))))
    answer = Script(host).ask(_probe("restore", expected=(PLAN[0], fake_db(PLAN[0])), target=START[0]))
    assert answer["status"] == "applied" and host.mixer.writes == [(2, START[0])]


# --- client: probes, pending journal, guard ---------------------------------------------------------------------------


def test_probe_is_journaled_pending_and_its_uncertain_outcome_blocks_all_writes():
    host = FakeHost()
    host.mixer.setTrackVolume = lambda i, v: (_ for _ in ()).throw(RuntimeError("x"))
    journal = []
    client = client_module.BridgeClient(FakeTransport(responder=Script(host)), FakeClock(0.01), journal=journal.append)
    outcome = client.probe(0, START[0], START[1])
    assert [r["status"] for r in journal] == ["pending"] and outcome.reply["status"] == "uncertain"
    assert client.write_block is not None
    assert client.probe(1, START[0], START[1]).status == "blocked"
    assert client.set_gain(TITLE, 2, "Bass", START[0], START[1], TARGET).status == "blocked"


def _calibration_client(host=None, step=0.001):
    script = Script(host or FakeHost(curve=hypothesis_db, volumes=((0.8, 0.0), (0.76, hypothesis_db(0.76)))))
    journal = []
    client = client_module.BridgeClient(FakeTransport(responder=script), FakeClock(step), journal=journal.append)
    return client, script, journal


def test_full_simulated_calibration_restores_the_exact_start():
    client, script, journal = _calibration_client()
    log = []
    client.journal = log.append
    summary = client_module.run_calibration(client, RUN, on_outcome=lambda o: log.append(client_module.record_of(o)))
    assert summary["status"] == "completed" and summary["restored_exact"] is True
    writes = script.host.mixer.writes
    assert [v for _, v in writes] == [*PLAN, 0.76]  # every pinned position once, in order, then the exact start
    assert script.host.mixer.volumes[1] == (0.76, hypothesis_db(0.76))
    ids = [r["command"]["id"] for r in log if r["command"]["type"] == "probe_gain" and r["status"] != "pending"]
    assert ids == [f"cal-{RUN}-{i:02d}" for i in range(len(PLAN))] + [f"cal-{RUN}-restore-1"]
    assert [r["status"] for r in log][:3] == ["replied", "pending", "replied"]  # inspect, then journaled probes
    assert client.write_block is None


def test_calibration_chains_expected_gain_from_each_read_back():
    client, script, _ = _calibration_client()
    log = []
    client_module.run_calibration(client, RUN, on_outcome=lambda o: log.append(client_module.record_of(o)))
    probes = [r for r in log if r["command"]["type"] == "probe_gain"]
    for previous, current in zip(probes, probes[1:]):
        assert current["command"]["expected"] == previous["reply"]["observed"]["after"]


def test_calibration_stops_on_uncertain_without_restoring():
    host = FakeHost(curve=hypothesis_db, volumes=((0.8, 0.0), (0.76, hypothesis_db(0.76))))
    original = host.mixer.setTrackVolume
    calls = []

    def fail_on_third(index, value):
        calls.append(value)
        if len(calls) == 3:
            raise RuntimeError("lost")
        original(index, value)

    host.mixer.setTrackVolume = fail_on_third
    client, script, _ = _calibration_client(host)
    summary = client_module.run_calibration(client, RUN)
    assert summary["status"] == "aborted" and summary["reason"] == "uncertain" and summary["restored_exact"] is False
    assert len(calls) == 3 and client.write_block is not None  # no restore attempt: inspect first


def test_calibration_restores_after_a_known_refusal():
    host = FakeHost(curve=hypothesis_db, volumes=((0.8, 0.0), (0.76, hypothesis_db(0.76))))
    script = Script(host)
    count = {"n": 0}

    def responder(raw):
        count["n"] += 1
        if count["n"] == 5:  # the 4th probe meets a playing transport
            host.transport.playing = True
            answer = script(raw)
            host.transport.playing = False
            return answer
        return script(raw)

    client = client_module.BridgeClient(FakeTransport(responder=responder), FakeClock(0.001))
    summary = client_module.run_calibration(client, RUN)
    assert summary["status"] == "aborted" and summary["reason"] == "precondition_failed:transport_playing"
    assert summary["restored_exact"] is True and host.mixer.volumes[1][0] == 0.76


def test_calibration_refuses_when_blocked_or_when_inspect_fails():
    client, script, _ = _calibration_client()
    client.write_block = {"project_title": TITLE, "insert": {"index": 2, "name": "Bass"}, "command_id": "x",
                          "reason": "timeout"}  # fmt: skip
    script.host.general.title = "Other Project"  # the inspect will mismatch, so the block stays
    summary = client_module.run_calibration(client, RUN)
    assert summary["status"] == "aborted" and script.host.mixer.writes == []
    client, script, _ = _calibration_client()
    client.write_block = {"project_title": TITLE, "insert": {"index": 2, "name": "Bass"}, "command_id": "x",
                          "reason": "timeout"}  # fmt: skip
    summary = client_module.run_calibration(client, RUN)  # an ok inspect of the plan's insert clears the block
    assert summary["status"] == "completed"


def test_restore_from_log_after_an_abort_uses_the_logged_start():
    host = FakeHost(curve=hypothesis_db, volumes=((0.8, 0.0), (0.76, hypothesis_db(0.76))))
    original = host.mixer.setTrackVolume

    calls = []

    def fail_on_fifth(index, value):  # the fifth write lands, then the setter raises: outcome uncertain
        calls.append(value)
        original(index, value)
        if len(calls) == 5:
            raise RuntimeError("acknowledgement lost")

    host.mixer.setTrackVolume = fail_on_fifth
    script = Script(host)
    log = []
    client = client_module.BridgeClient(FakeTransport(responder=script), FakeClock(0.001), journal=log.append)
    summary = client_module.run_calibration(client, RUN, on_outcome=lambda o: log.append(client_module.record_of(o)))
    assert summary["reason"] == "uncertain"
    reloaded = Script(host)  # e.g. script reloaded: no session start recorded
    fresh = client_module.BridgeClient(FakeTransport(responder=reloaded), FakeClock(0.001),
                                       write_block=client_module.write_block_from_records(log),
                                       journal=log.append)  # fmt: skip
    result = client_module.restore_calibration(fresh, log, RUN,
                                               on_outcome=lambda o: log.append(client_module.record_of(o)))  # fmt: skip
    assert result["status"] == "restored" and result["restored_exact"] is True
    assert host.mixer.volumes[1][0] == 0.76 and fresh.write_block is None
    assert log[-2]["command"]["id"] == f"cal-{RUN}-restore-1"


def test_cli_calibrate_needs_a_log_and_refuses_when_blocked(tmp_path, capsys):
    with pytest.raises(SystemExit):
        client_module.main(["calibrate"])
    log = tmp_path / "cal.jsonl"
    log.write_text(json.dumps({"status": "timeout", "command": _write(), "reply": None}) + "\n", encoding="utf-8")
    assert client_module.main(["calibrate", "--log", str(log), "--out", "missing", "--in", "missing"]) == 3
    # blocked: the runner's first inspect may clear it, so the CLI opens the ports; only missing ports stop it here


# --- evaluation against the predeclared criteria --------------------------------------------------------------------


def _simulated_log(curve=hypothesis_db, run_id=RUN, read_back=None, host=None):
    host = host or FakeHost(curve=curve, volumes=((0.8, 0.0), (0.76, curve(0.76))))
    if read_back is not None:
        original_read = host.mixer.getTrackVolume
        host.mixer.getTrackVolume = lambda i, mode=0: read_back(i, mode, original_read)
    script = Script(host)
    log = []
    client = client_module.BridgeClient(FakeTransport(responder=script), FakeClock(0.001), journal=log.append)
    client_module.run_calibration(client, run_id, on_outcome=lambda o: log.append(client_module.record_of(o)))
    return log


def test_h_reproduces_the_three_live_points_that_selected_it():
    assert evaluator.h_db(0.8) == pytest.approx(0.0, abs=1e-12)
    assert evaluator.h_db(0.7800000002607703) == pytest.approx(-0.5745229721069336, abs=1e-5)
    assert evaluator.h_db(0.7599999997764826) == pytest.approx(-1.1527416706085205, abs=1e-5)


def test_evaluation_accepts_h_when_the_fader_follows_it():
    result = evaluator.evaluate(_simulated_log(), RUN)
    assert result["verdict"] == "H_accepted" and result["complete"] is True
    assert result["criteria"] == {"normalized_exact": True, "primary_within_tolerance": True,
                                  "visits_agree": True}  # fmt: skip
    assert result["accepted_range"] == [0.62, 0.86] and result["restore"]["exact"] is True
    assert len(result["probes"]) == len(PLAN)
    assert result["tolerances"] == {"db": 0.01, "normalized": 1e-6, "visits_db": 1e-6}


def test_evaluation_rejects_h_when_one_primary_point_misses_by_more_than_0_01_db():
    curve = lambda v: hypothesis_db(v) + (0.02 if abs(v - 0.71) < 1e-12 else 0.0)  # noqa: E731
    result = evaluator.evaluate(_simulated_log(curve), RUN)
    assert result["verdict"] in ("table_accepted", "not_established")
    assert result["criteria"]["primary_within_tolerance"] is False
    assert result["fallback"]["fit_points"] == [0.62, 0.68, 0.73, 0.77, 0.81, 0.86]
    assert result["fallback"]["held_out_points"] == [0.65, 0.71, 0.75, 0.79, 0.83]


def test_evaluation_fallback_table_accepts_a_smooth_curve_that_is_not_h():
    curve = lambda v: hypothesis_db(v) + 0.2 * (v - 0.74)  # noqa: E731  (smooth tilt: H misses by 0.024, a table fits)
    result = evaluator.evaluate(_simulated_log(curve), RUN)
    assert result["criteria"]["primary_within_tolerance"] is False and result["verdict"] == "table_accepted"


def test_evaluation_detects_hysteresis_between_the_two_visits():
    host = FakeHost(curve=hypothesis_db, volumes=((0.8, 0.0), (0.76, hypothesis_db(0.76))))
    original = host.mixer.setTrackVolume
    visits = []

    def second_visit_higher(index, value):  # the second visit of 0.77 lands 0.001 dB higher
        original(index, value)
        if value == 0.77:
            visits.append(value)
            if len(visits) == 2:
                host.mixer.volumes[index - 1] = (value, hypothesis_db(value) + 0.001)

    host.mixer.setTrackVolume = second_visit_higher
    result = evaluator.evaluate(_simulated_log(host=host), RUN)
    assert result["criteria"]["visits_agree"] is False and result["verdict"] != "H_accepted"


def test_evaluation_detects_a_stored_value_that_differs_from_the_target():
    curve = hypothesis_db
    host_round = lambda i, mode, original: original(i, mode) - (2e-6 if mode == 0 else 0.0)  # noqa: E731
    result = evaluator.evaluate(_simulated_log(curve, read_back=host_round), RUN)
    assert result["criteria"]["normalized_exact"] is False and result["verdict"] != "H_accepted"


def test_evaluation_reports_an_incomplete_run():
    log = _simulated_log()
    first_of_step_7 = next(i for i, r in enumerate(log) if r["command"].get("step") == 7)
    result = evaluator.evaluate(log[:first_of_step_7], RUN)  # a run that stopped before step 7
    assert result["problems"] == []
    assert result["complete"] is False and result["verdict"] == "incomplete"
    assert result["missing_steps"] == list(range(7, len(PLAN)))


# --- evaluation refuses records it cannot trust ------------------------------------------------------------------------


def _copy(log):
    return json.loads(json.dumps(log))


def _replied_probe(log, step):
    return next(r for r in log if r["status"] == "replied" and r["command"].get("step") == step)


def _invalid(log, needle):
    result = evaluator.evaluate(log, RUN)
    assert result["verdict"] == "invalid_records", result["verdict"]
    assert result["accepted_range"] is None
    assert any(needle in problem for problem in result["problems"]), result["problems"]
    return result


def test_untampered_simulated_log_has_no_problems():
    assert evaluator.evaluate(_simulated_log(), RUN)["problems"] == []


def test_evaluation_refuses_a_reply_from_another_project():
    log = _copy(_simulated_log())
    _replied_probe(log, 3)["reply"]["observed"]["project_title"] = "Other Project"
    _invalid(log, "write_identity_differs")


def test_evaluation_refuses_a_probe_command_for_another_insert():
    log = _copy(_simulated_log())
    for record in log:
        if record["command"].get("step") == 5:
            record["command"]["insert"] = {"index": 1, "name": "Kick"}
            if record["reply"]:
                record["reply"]["observed"]["insert"] = {"index": 1, "name": "Kick"}
    _invalid(log, "command plan")


def test_evaluation_refuses_an_initial_inspect_of_another_project():
    log = _copy(_simulated_log())
    log[0]["command"]["project_title"] = log[0]["reply"]["observed"]["project_title"] = "Other Project"
    _invalid(log, "initial inspect")


def test_evaluation_refuses_a_changed_target_even_when_command_and_reply_agree():
    log = _copy(_simulated_log())
    for record in log:
        if record["command"].get("step") == 4:  # pinned 0.81, rewritten to 0.80 throughout
            record["command"]["target_normalized"] = 0.80
            if record["reply"]:
                record["reply"]["observed"]["after"] = {"normalized": 0.80, "db": hypothesis_db(0.80)}
    _invalid(log, "command plan")


def test_evaluation_refuses_a_read_back_that_contradicts_the_target():
    log = _copy(_simulated_log())
    observed = _replied_probe(log, 4)["reply"]["observed"]
    observed["after"] = {"normalized": 0.70, "db": hypothesis_db(0.70)}
    observed["db_change"] = observed["after"]["db"] - observed["before"]["db"]
    _invalid(log, "applied_values_inconsistent")


def test_evaluation_refuses_swapped_probe_order():
    log = _copy(_simulated_log())
    third = [i for i, r in enumerate(log) if r["command"].get("step") == 3]
    fourth = [i for i, r in enumerate(log) if r["command"].get("step") == 4]
    block_3, block_4 = [log[i] for i in third], [log[i] for i in fourth]
    start = min(third)
    log[start : start + len(third) + len(fourth)] = block_4 + block_3
    _invalid(log, "order")


def test_evaluation_refuses_a_broken_chain_between_probes():
    log = _copy(_simulated_log())
    for record in log:
        if record["command"].get("step") == 6:
            record["command"]["expected"] = {"normalized": 0.5, "db": hypothesis_db(0.5)}
            if record["reply"]:
                record["reply"]["observed"]["before"] = {"normalized": 0.5, "db": hypothesis_db(0.5)}
                after = record["reply"]["observed"]["after"]
                record["reply"]["observed"]["db_change"] = after["db"] - hypothesis_db(0.5)
    _invalid(log, "chain")


def test_evaluation_refuses_a_gap_in_the_sequence():
    log = [r for r in _simulated_log() if r["command"].get("step") != 7]
    _invalid(log, "order")


def test_evaluation_refuses_an_id_that_does_not_match_its_step():
    log = _copy(_simulated_log())
    for record in log:
        if record["command"].get("step") == 2:
            record["command"]["id"] = f"cal-{RUN}-09"
            if record["reply"]:
                record["reply"]["id"] = f"cal-{RUN}-09"
    _invalid(log, "id")


def test_evaluation_refuses_a_replay_whose_observations_differ():
    log = _copy(_simulated_log())
    replay = _copy([_replied_probe(log, 8)])[0]
    replay["reply"]["observed"]["replayed"] = True
    replay["reply"]["observed"]["after"]["db"] += 0.001
    replay["reply"]["observed"]["db_change"] += 0.001
    _invalid(log + [replay], "replay")


def test_evaluation_refuses_a_malformed_reply_and_a_wrong_restore_target():
    log = _copy(_simulated_log())
    _replied_probe(log, 10)["reply"]["observed"]["replayed"] = "no"
    _invalid(log, "reply field")
    log = _copy(_simulated_log())
    for record in log:
        if record["command"].get("step") == "restore":
            record["command"]["target_normalized"] = 0.7
            if record["reply"]:
                record["reply"]["observed"]["after"] = {"normalized": 0.7, "db": hypothesis_db(0.7)}
                record["reply"]["observed"]["db_change"] = (
                    hypothesis_db(0.7) - record["reply"]["observed"]["before"]["db"]
                )
    _invalid(log, "restore")


def test_evaluation_uses_only_the_named_run_and_counts_replays_once():
    log = _simulated_log(run_id="runA") + _simulated_log(run_id="runB")
    result = evaluator.evaluate(log, "runB")
    assert len(result["probes"]) == len(PLAN) and all(p["id"].startswith("cal-runB-") for p in result["probes"])
    replay = [dict(r) for r in log if r["status"] == "replied" and r["command"]["id"] == "cal-runB-03"]
    replay[0]["reply"] = {**replay[0]["reply"], "observed": {**replay[0]["reply"]["observed"], "replayed": True}}
    assert len(evaluator.evaluate(log + replay, "runB")["probes"]) == len(PLAN)


def test_evaluation_cli_writes_json(tmp_path):
    log = tmp_path / "cal.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in _simulated_log()), encoding="utf-8")
    out = tmp_path / "result.json"
    assert evaluator.main(["--log", str(log), "--run-id", RUN, "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "H_accepted"


# === set_gain_db (calibrated dB cut) ================================================================================

LIVE_START = (0.7599999997764826, -1.1527416706085205)  # restored start after the live calibration
LO, HI = protocol.CONVERSION_RANGE


def _h_host(start=LIVE_START, curve=None, fl_version=None):
    host = FakeHost(curve=curve or protocol.h_db, volumes=((0.8, 0.0), start))
    if fl_version is not None:
        host.ui.getVersion = lambda mode=0: fl_version
    return host


def _db(delta=-1.0, start=LIVE_START, command_id_=CMD_ID, title=TITLE, index=2, name="Bass"):
    return protocol.set_gain_db_command(command_id_, title, index, name, start[0], start[1], delta)


def _raw_db(**changes):
    command = _db()
    command.update(changes)
    return command


# --- the versioned conversion ---------------------------------------------------------------------------------------


def test_conversion_is_versioned_and_scoped_to_the_calibrated_build_and_insert():
    assert protocol.CONVERSION_ID == "fl-mixer-h125-v1"
    assert protocol.CONVERSION_PLAN == protocol.CALIBRATION_PLAN_ID
    assert protocol.CONVERSION_RANGE == (0.62, 0.86)
    assert protocol.CONVERSION_SCOPE == {"project_title": TITLE, "insert": {"index": 2, "name": "Bass"},
                                         "fl_version": "Producer Edition v26.1.7 [build 5654]"}  # fmt: skip
    assert protocol.DB_CUT_RANGE == (-3.0, -0.1)
    assert protocol.CONVERSION_MODEL_TOLERANCE_DB == 0.01 and protocol.DB_SUCCESS_TOLERANCE == 0.05


def test_protocol_curve_is_the_evaluated_curve():
    for v in (*PLAN, 0.7599999997764826, 0.7800000002607703, 0.8):
        assert protocol.h_db(v) == evaluator.h_db(v)


def test_inverse_is_consistent_across_the_calibrated_range():
    for i in range(241):
        v = LO + (HI - LO) * i / 240
        assert protocol.h_inverse(protocol.h_db(v)) == pytest.approx(v, abs=1e-12)
        d = protocol.h_db(LO) + (protocol.h_db(HI) - protocol.h_db(LO)) * i / 240
        assert protocol.h_db(protocol.h_inverse(d)) == pytest.approx(d, abs=1e-9)


@pytest.mark.parametrize("delta", [-1.0, -2.0, -3.0])
def test_target_is_one_absolute_position_from_the_read_back_db_plus_the_cut(delta):
    target = protocol.db_target(LIVE_START[1], delta)
    assert protocol.h_db(target) == pytest.approx(LIVE_START[1] + delta, abs=1e-9)
    assert LO <= target < LIVE_START[0]
    assert _db(delta)["target_normalized"] == target


# --- command validation ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("delta", [-0.1, -1.0, -2.0, -3.0])
def test_cuts_from_0_1_to_3_db_are_accepted(delta):
    assert protocol.validate_command(_db(delta))["intended_db_change"] == delta


@pytest.mark.parametrize("delta", [-0.09, -3.01, 0.0, 0.5, -10.0])
def test_cuts_outside_0_1_to_3_db_are_refused(delta):
    with pytest.raises(protocol.ProtocolError) as info:
        _db(delta)
    assert info.value.code == "bounds"


def _start_at(v):
    return (v, protocol.h_db(v))


@pytest.mark.parametrize(
    ("start", "delta", "code"),
    [
        (_start_at(0.8605), -0.1, "range"),  # start above the calibrated range
        (_start_at(0.6195), -0.1, "range"),  # start below it
        (_start_at(0.62), -0.1, "range"),  # start on the lower edge: any cut leaves the range
        (_start_at(0.64), -1.0, "range"),  # target below 0.62
        (_start_at(0.95), -1.0, "range"),
    ],
)
def test_start_and_target_must_lie_inside_the_calibrated_range(start, delta, code):
    with pytest.raises(protocol.ProtocolError) as info:
        _db(delta, start)
    assert info.value.code == code


def test_range_edges_are_inclusive():
    assert _db(-0.1, _start_at(0.86))  # start on the upper edge
    edge_start = protocol.h_inverse(protocol.h_db(LO) + 3.0)  # a 3 dB cut that lands exactly on 0.62
    command = _db(-3.0, _start_at(edge_start))
    assert command["target_normalized"] == pytest.approx(LO, abs=1e-9)
    with pytest.raises(protocol.ProtocolError):
        _db(-3.0, _start_at(edge_start - 1e-6))


def test_a_start_the_conversion_does_not_describe_is_refused():
    with pytest.raises(protocol.ProtocolError) as info:
        _db(-1.0, (0.76, protocol.h_db(0.76) + 0.011))  # read-back dB 0.011 dB off the calibrated curve
    assert info.value.code == "uncalibrated_start"
    assert _db(-1.0, (0.76, protocol.h_db(0.76) + 0.009))


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"conversion": "fl-mixer-h125-v0"}, "conversion"),
        ({"conversion": "other"}, "conversion"),
        ({"project_title": "Other Project"}, "conversion_scope"),
        ({"insert": {"index": 1, "name": "Kick"}}, "conversion_scope"),
        ({"target_normalized": _db()["target_normalized"] + 1e-6}, "conversion_mismatch"),
        ({"target_normalized": _db()["target_normalized"] - 1e-6}, "conversion_mismatch"),
        ({"intended_db_change": True}, "field"),
        ({"intended_db_change": float("nan")}, "field"),
        ({"expected": {"normalized": 0.76}}, "field"),
        ({"extra": 1}, "field"),
    ],
)
def test_set_gain_db_refuses_wrong_conversion_scope_and_disagreeing_targets(changes, code):
    with pytest.raises(protocol.ProtocolError) as info:
        protocol.validate_command(_raw_db(**changes))
    assert info.value.code == code


def test_a_target_within_1e_9_of_the_recomputed_one_is_accepted():
    assert protocol.validate_command(_raw_db(target_normalized=_db()["target_normalized"] + 5e-10))


def test_set_gain_db_missing_fields_and_cross_type_fields_are_refused():
    for key in ("expected", "intended_db_change", "target_normalized", "conversion"):
        message = _raw_db()
        del message[key]
        with pytest.raises(protocol.ProtocolError):
            protocol.validate_command(message)
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command({**_write(), "conversion": protocol.CONVERSION_ID})


def test_worst_case_set_gain_db_messages_fit_the_size_limit():
    command = {**_db(), "id": "x" * 64, "intended_db_change": LONG_NUMBER}
    gain = {"normalized": 0.7812345678901234, "db": LONG_NUMBER}
    observed = {"project_title": TITLE, "fl_version": "v" * protocol.MAX_VERSION_BYTES, "api": 2**31 - 1,
                "insert": {"index": 2, "name": "Bass"}, "before": gain, "after": gain, "db_change": LONG_NUMBER,
                "replayed": False}  # fmt: skip
    for message in (command, protocol.reply("x" * 64, "applied_off_target", observed, "e" * protocol.MAX_ERROR_BYTES)):
        assert len(protocol.encode(message)) <= protocol.MAX_MESSAGE_BYTES


# --- replies -----------------------------------------------------------------------------------------------------------


def _db_reply(status="applied", delta=-1.0, miss=0.0, error=None, start=LIVE_START):
    target = protocol.db_target(start[1], delta)
    after = (target, start[1] + delta + miss)
    observed = _write_observed(before=start, after=after)
    return protocol.reply(CMD_ID, status, observed, error)


def test_applied_off_target_replies_are_validated():
    assert protocol.validate_reply(_db_reply("applied_off_target", miss=-0.08, error="db_off_target"))
    for bad in (
        _db_reply("applied_off_target", miss=-0.08),  # no error named
        {**_db_reply("applied_off_target", miss=-0.08, error="db_off_target"), "observed": None},
    ):
        with pytest.raises(protocol.ProtocolError):
            protocol.validate_reply(bad)
    bad = _db_reply("applied_off_target", miss=-0.08, error="db_off_target")
    bad["observed"]["after"] = None
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_reply(bad)


def test_set_gain_db_reply_consistency_judges_both_read_backs():
    command = _db()
    assert protocol.reply_consistency(command, _db_reply()) is None
    assert protocol.reply_consistency(command, _db_reply(miss=0.049)) is None
    assert protocol.reply_consistency(command, _db_reply(miss=0.06)) == "applied_values_inconsistent"  # missed: not ok
    off = _db_reply("applied_off_target", miss=-0.08, error="db_off_target")
    assert protocol.reply_consistency(command, off) is None
    not_off = _db_reply("applied_off_target", miss=-0.01, error="db_off_target")
    assert protocol.reply_consistency(command, not_off) == "applied_values_inconsistent"
    moved = _db_reply()
    moved["observed"]["after"]["normalized"] += 0.002
    assert protocol.reply_consistency(command, moved) == "applied_values_inconsistent"
    assert protocol.reply_consistency(_write(), off) == "status_not_for_type"  # only dB commands miss dB
    assert protocol.reply_consistency(command, _ok()) == "status_not_for_type"


# --- FL Studio script ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("delta", [-1.0, -2.0, -3.0])
def test_set_gain_db_applies_one_write_and_meets_the_requested_db(delta):
    script = Script(_h_host())
    command = _db(delta)
    answer = _accepted(command, script.ask(command))
    assert answer["status"] == "applied" and script.host.mixer.writes == [(2, command["target_normalized"])]
    obs = answer["observed"]
    assert obs["before"] == {"normalized": LIVE_START[0], "db": LIVE_START[1]}
    assert obs["db_change"] == pytest.approx(delta, abs=1e-9)


def test_a_known_write_that_misses_the_requested_db_is_applied_off_target_not_uncertain():
    host = _h_host()
    original = host.mixer.setTrackVolume

    def lands_low(index, value):  # the fader lands where commanded, but reads 0.08 dB lower than the curve says
        original(index, value)
        host.mixer.volumes[index - 1] = (value, protocol.h_db(value) - 0.08)

    host.mixer.setTrackVolume = lands_low
    answer = _accepted(_db(), Script(host).ask(_db()))
    assert answer["status"] == "applied_off_target" and answer["error"] == "db_off_target"
    assert answer["observed"]["after"]["db"] == pytest.approx(LIVE_START[1] - 1.08, abs=1e-9)  # observed state kept
    assert host.mixer.writes == [(2, _db()["target_normalized"])]


def test_set_gain_db_fader_elsewhere_is_uncertain():
    host = _h_host()
    original = host.mixer.setTrackVolume

    def elsewhere(index, value):
        original(index, value)
        host.mixer.volumes[index - 1] = (value - 0.01, protocol.h_db(value - 0.01))

    host.mixer.setTrackVolume = elsewhere
    answer = Script(host).ask(_db())
    assert answer["status"] == "uncertain" and answer["error"] == "readback_disagrees"


@pytest.mark.parametrize(
    "failure",
    ["setter", "read_back", "none_db"],
)
def test_set_gain_db_interruptions_after_the_setter_are_uncertain(failure):
    host = _h_host()
    original_set, original_read = host.mixer.setTrackVolume, host.mixer.getTrackVolume
    if failure == "setter":
        host.mixer.setTrackVolume = lambda i, v: (original_set(i, v), (_ for _ in ()).throw(RuntimeError("x")))
    elif failure == "read_back":
        host.mixer.getTrackVolume = lambda i, mode=0: (
            (_ for _ in ()).throw(OSError("x")) if host.mixer.writes else original_read(i, mode)
        )
    else:
        host.mixer.setTrackVolume = lambda i, v: (original_set(i, v), host.mixer.volumes.__setitem__(i - 1, (v, None)))
    script = Script(host)
    answer = _accepted(_db(), script.ask(_db()))
    assert answer["status"] == "uncertain" and len(host.mixer.writes) == 1
    assert script.ask(_db())["status"] == "uncertain" and len(host.mixer.writes) == 1  # replayed, not rewritten


@pytest.mark.parametrize(
    ("setup", "status", "error"),
    [
        (lambda h: setattr(h.general, "title", "Other Project"), "identity_mismatch", "project_title"),
        (lambda h: h.mixer.names.__setitem__(1, "Sub"), "identity_mismatch", "insert"),
        (lambda h: setattr(h.transport, "playing", True), "precondition_failed", "transport_playing"),
        (lambda h: setattr(h.general, "safe", False), "precondition_failed", "edit_unavailable"),
        (lambda h: h.mixer.volumes.__setitem__(1, (0.7615, protocol.h_db(0.7615))), "precondition_failed",
         "start_gain"),
        (lambda h: setattr(h.ui, "getVersion", lambda mode=0: "Producer Edition v26.2.0 [build 6000]"),
         "precondition_failed", "uncalibrated_build"),
        (lambda h: h.mixer.volumes.__setitem__(1, (LIVE_START[0], LIVE_START[1] + 0.04)), "precondition_failed",
         "uncalibrated_start"),
        (lambda h: setattr(h.general, "api", 37), "error", "api_too_old"),
    ],
)  # fmt: skip
def test_set_gain_db_keeps_every_pre_write_check(setup, status, error):
    host = _h_host()
    setup(host)
    answer = Script(host).ask(_db())
    assert (answer["status"], answer["error"]) == (status, error)
    assert host.mixer.writes == []


def test_set_gain_db_refuses_an_actual_start_outside_the_range_within_the_start_tolerance():
    host = _h_host(start=_start_at(0.8605))  # expected 0.86 is inside; the fader really sits at 0.8605
    command = _db(-0.5, _start_at(0.86))
    answer = Script(host).ask(command)
    assert (answer["status"], answer["error"]) == ("precondition_failed", "range") and host.mixer.writes == []


@pytest.mark.parametrize(
    ("changes", "code"),
    [({"conversion": "fl-mixer-h125-v0"}, "conversion"),
     ({"target_normalized": _db()["target_normalized"] + 1e-6}, "conversion_mismatch")],
)  # fmt: skip
def test_the_script_recomputes_and_rejects_disagreement_before_any_write(changes, code):
    host = _h_host()
    answer = protocol.decode(Script(host)(protocol.encode(_raw_db(**changes))))
    assert (answer["status"], answer["error"]) == ("invalid_command", code) and host.mixer.writes == []


def test_set_gain_db_duplicate_delivery_replays_and_conflict_is_refused():
    script = Script(_h_host())
    first = script.ask(_db(-1.0))
    second = script.ask(_db(-1.0))
    assert second["observed"]["replayed"] is True and {**second["observed"], "replayed": False} == first["observed"]
    assert script.ask(_db(-2.0))["error"] == "duplicate_id_conflict"
    assert len(script.host.mixer.writes) == 1


def test_an_off_target_result_is_replayed_too():
    host = _h_host()
    original = host.mixer.setTrackVolume
    host.mixer.setTrackVolume = lambda i, v: (original(i, v), host.mixer.volumes.__setitem__(i - 1,
                                              (v, protocol.h_db(v) - 0.08)))  # fmt: skip
    script = Script(host)
    assert script.ask(_db())["status"] == "applied_off_target"
    again = script.ask(_db())
    assert again["status"] == "applied_off_target" and again["observed"]["replayed"] is True
    assert len(host.mixer.writes) == 1


# --- client: conversion, conversion block, guard, interruption ----------------------------------------------------


def test_client_set_gain_db_round_trip_sends_the_shared_target():
    script = Script(_h_host())
    client, transport = _client(script)
    outcome = client.set_gain_db(TITLE, 2, "Bass", LIVE_START[0], LIVE_START[1], -2.0, command_id=CMD_ID)
    assert outcome.status == "replied" and outcome.reply["status"] == "applied"
    assert protocol.decode(transport.sent[0])["target_normalized"] == protocol.db_target(LIVE_START[1], -2.0)
    assert client.write_block is None and client.conversion_block is None


def test_client_refuses_out_of_range_and_out_of_bounds_cuts_locally():
    client, transport = _client(Script(_h_host()))
    for delta, start in ((-3.5, LIVE_START), (-0.05, LIVE_START), (-1.0, _start_at(0.64))):
        with pytest.raises(protocol.ProtocolError):
            client.set_gain_db(TITLE, 2, "Bass", start[0], start[1], delta)
    assert transport.sent == []


def _off_target_client():
    host = _h_host()
    original = host.mixer.setTrackVolume
    host.mixer.setTrackVolume = lambda i, v: (original(i, v), host.mixer.volumes.__setitem__(i - 1,
                                              (v, protocol.h_db(v) - 0.08)))  # fmt: skip
    journal = []
    client = client_module.BridgeClient(FakeTransport(responder=Script(host)), FakeClock(0.01), journal=journal.append)
    return client, host, journal


def test_off_target_sets_the_conversion_block_but_not_the_write_guard():
    client, host, _ = _off_target_client()
    outcome = client.set_gain_db(TITLE, 2, "Bass", LIVE_START[0], LIVE_START[1], -1.0)
    assert outcome.reply["status"] == "applied_off_target"
    assert client.write_block is None  # the state is known
    assert client.conversion_block["conversion"] == protocol.CONVERSION_ID
    after = outcome.reply["observed"]["after"]
    # a well-formed next dB command (on-curve start) is held by the block; nothing is sent
    assert client.set_gain_db(TITLE, 2, "Bass", LIVE_START[0], LIVE_START[1], -0.5).status == "blocked"
    assert len(host.mixer.writes) == 1
    plain = client.set_gain(TITLE, 2, "Bass", after["normalized"], after["db"], after["normalized"] - 0.01)
    assert plain.status == "replied"  # normalized set_gain stays available


def test_conversion_block_is_rebuilt_from_the_log():
    client, _, journal = _off_target_client()
    log = list(journal)
    outcome = client.set_gain_db(TITLE, 2, "Bass", LIVE_START[0], LIVE_START[1], -1.0)
    log = journal + [client_module.record_of(outcome)]
    assert client_module.conversion_block_from_records(log)["conversion"] == protocol.CONVERSION_ID
    assert client_module.conversion_block_from_records(log[:-1]) is None
    other = {**log[-1], "reply": {**log[-1]["reply"], "status": "applied", "error": None}}
    assert client_module.conversion_block_from_records([other]) is None


def test_uncertain_db_write_blocks_all_writes_and_not_the_conversion():
    host = _h_host()
    host.mixer.setTrackVolume = lambda i, v: (_ for _ in ()).throw(RuntimeError("x"))
    client, _ = _client(Script(host))
    outcome = client.set_gain_db(TITLE, 2, "Bass", LIVE_START[0], LIVE_START[1], -1.0)
    assert outcome.reply["status"] == "uncertain" and client.write_block is not None
    assert client.conversion_block is None
    assert client.set_gain_db(TITLE, 2, "Bass", LIVE_START[0], LIVE_START[1], -1.0).status == "blocked"


def test_db_write_timeout_and_failed_dispatch_leave_the_write_pending_and_blocked():
    journal = []
    client = client_module.BridgeClient(FakeTransport(), FakeClock(0.5), journal=journal.append)
    lost = client.set_gain_db(TITLE, 2, "Bass", LIVE_START[0], LIVE_START[1], -1.0, timeout_s=1.0)
    assert lost.status == "timeout" and client.write_block is not None
    assert [r["status"] for r in journal] == ["pending"]

    class BrokenTransport(FakeTransport):
        def send(self, raw):
            raise OSError("midiOutLongMsg failed")

    client = client_module.BridgeClient(BrokenTransport(), FakeClock(0.01), journal=journal.append)
    with pytest.raises(OSError):
        client.set_gain_db(TITLE, 2, "Bass", LIVE_START[0], LIVE_START[1], -1.0, command_id=CMD_ID)
    assert client.write_block["reason"] == "pending"


def test_client_rejects_an_applied_reply_whose_db_missed():
    client, _ = _client(replies=[protocol.encode(_db_reply(miss=0.06))])
    outcome = client.set_gain_db(TITLE, 2, "Bass", LIVE_START[0], LIVE_START[1], -1.0, command_id=CMD_ID)
    assert outcome.status == "invalid_reply" and outcome.malformed_replies[0]["error"] == "applied_values_inconsistent"
    assert client.write_block is not None


def _db_cli_args(tmp_path, delta="-1.0", start=LIVE_START):
    return ["set-gain-db", "--project-title", TITLE, "--insert-index", "2", "--insert-name", "Bass",
            "--expected-normalized", repr(start[0]), "--expected-db", repr(start[1]), "--db-change", delta,
            "--log", str(tmp_path / "db.jsonl"), "--out", "missing", "--in", "missing"]  # fmt: skip


def test_cli_set_gain_db_refuses_locally_and_when_the_conversion_is_blocked(tmp_path, capsys):
    assert client_module.main(_db_cli_args(tmp_path, "-3.5")) == 6
    assert "bounds" in capsys.readouterr().err
    client, _, journal = _off_target_client()
    outcome = client.set_gain_db(TITLE, 2, "Bass", LIVE_START[0], LIVE_START[1], -1.0)
    (tmp_path / "db.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in [*journal, client_module.record_of(outcome)]), encoding="utf-8"
    )
    assert client_module.main(_db_cli_args(tmp_path)) == 5
    assert "conversion" in capsys.readouterr().err


def test_calibration_restore_can_read_its_start_from_another_log(tmp_path):
    cal_log = _simulated_log()  # a completed calibration run (start 0.76 on the H fake)
    host = FakeHost(curve=hypothesis_db, volumes=((0.8, 0.0), (0.7257, hypothesis_db(0.7257))))
    trial_log = []
    client = client_module.BridgeClient(FakeTransport(responder=Script(host)), FakeClock(0.001),
                                        journal=trial_log.append)  # fmt: skip
    result = client_module.restore_calibration(client, cal_log, RUN, records_for_attempts=trial_log,
                                               on_outcome=lambda o: trial_log.append(client_module.record_of(o)))  # fmt: skip
    assert result["status"] == "restored" and host.mixer.volumes[1][0] == 0.76
    assert trial_log[-2]["command"]["id"] == f"cal-{RUN}-restore-2"  # restore-1 is already in the calibration log
