"""Child process for the Increment 5 subprocess tests (ADR-039); not collected by pytest.

Usage: ``python restart_child.py <directory> <phase> <scenario> <crash>``. ``phase`` is ``start`` (a fresh durable run),
``resume`` (reopen, then resume a reloaded suspension with the scenario's next export) or ``hold`` (open the store and
wait to be killed). ``crash`` names the labelled point at which the process ends abruptly with ``os._exit``: no cleanup,
no closing, as after a crash. The outcome is written to ``<directory>/child.json``.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from daw_continuation_support import ACCEPT, INTERMEDIATE, NO_IMPROVEMENT, limits  # noqa: E402
from restart_support import close, durable, reopen  # noqa: E402
from test_coordinator import no_change, propose  # noqa: E402
from test_fl_runtime_recovery_controller import DeviceTransport  # noqa: E402

from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal  # noqa: E402
from autonomous_mixing.core.records.acceptance import AcceptanceEvent  # noqa: E402
from autonomous_mixing.core.records.daw_correction import DawContinuationStarted  # noqa: E402
from autonomous_mixing.core.records.run_report import RunReport, RunSuspended  # noqa: E402
from autonomous_mixing.core.records.run_restart import RunInterrupted  # noqa: E402
from autonomous_mixing.core.records.run_trace import DispatchIntent, OperationKind, OperationObserved  # noqa: E402

CRASHED = 77


def crash():
    os._exit(CRASHED)


def crash_decision(situation, number):
    crash()


SCENARIOS = {
    "single": dict(decisions=(propose(0.0),), master=ACCEPT),
    "rejected": dict(decisions=(propose(0.0), no_change), master=NO_IMPROVEMENT),
    "intermediate": dict(decisions=(propose(0.0), propose(-0.5)), master=INTERMEDIATE),
    "first_decision_crashes": dict(decisions=(crash_decision,), master=ACCEPT),
    "second_decision_crashes": dict(decisions=(propose(0.0), crash_decision), master=INTERMEDIATE),
}


class Hook:
    """The durable journal with labelled crash points (delegates everything else)."""

    def __init__(self, inner, after_append=None, commit=None):
        self.inner, self.after_append, self.commit_mode, self.applies = inner, after_append, commit, 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def append(self, event):
        result = self.inner.append(event)
        if self.after_append and self.after_append(self, event.payload):
            crash()
        return result

    def commit(self, event):
        if self.commit_mode in ("commit_before", "commit_after"):
            real = self.inner._commit

            def failing():
                if self.commit_mode == "commit_after":
                    real()
                crash()

            self.inner._commit = failing
        result = self.inner.commit(event)
        if self.commit_mode == "after_acceptance_commit":
            crash()
        return result


def nth_write(n, observed=False):
    def predicate(hook, payload):
        if observed:
            hit = isinstance(payload, OperationObserved) and payload.operation is OperationKind.DAW_APPLY
        else:
            hit = isinstance(payload, DispatchIntent) and payload.operation is OperationKind.DAW_APPLY
        if hit:
            hook.applies += 1
        return hit and hook.applies == n

    return predicate


APPEND_POINTS = {
    "after_apply_intent": nth_write(1),
    "after_second_write_intent": nth_write(1),  # counted in the resuming process: the run's second write
    "after_second_write_observed": nth_write(1, observed=True),
    "after_export_prepare": lambda hook, p: (
        isinstance(p, OperationObserved) and p.operation is OperationKind.EXPORT_PREPARE
    ),
    "after_continuation_started": lambda hook, p: isinstance(p, DawContinuationStarted),
    "before_acceptance_commit": lambda hook, p: isinstance(p, AcceptanceEvent),
}


def install(crash_point):
    if crash_point == "after_fl_reservation":
        real = SqliteCommandJournal.reserve

        def reserve(self, *args, **kwargs):
            real(self, *args, **kwargs)
            crash()

        SqliteCommandJournal.reserve = reserve
    elif crash_point == "after_device_write":
        real_send = DeviceTransport.send

        def send(self, raw):
            real_send(self, raw)
            if self.world.sent[-1]["type"] == "set_gain_db":
                crash()

        DeviceTransport.send = send
    elif crash_point == "after_fl_outcome":
        real_finish = SqliteCommandJournal.finish

        def finish(self, command_id, outcome):
            real_finish(self, command_id, outcome)
            if command_id.startswith("apply-"):
                crash()

        SqliteCommandJournal.finish = finish
    elif crash_point == "after_restore_reservation":
        real_restore = SqliteCommandJournal.reserve_restore

        def reserve_restore(self, *args, **kwargs):
            real_restore(self, *args, **kwargs)
            crash()

        SqliteCommandJournal.reserve_restore = reserve_restore


def main():
    directory, phase, scenario, crash_point = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    spec = SCENARIOS[scenario]
    budget = limits(max_recovery_operations=5)
    install(crash_point)
    if phase == "start":
        h = durable(directory, decisions=spec["decisions"], fresh=True, budget=budget)
    else:
        h = durable(directory, decisions=spec["decisions"][1:] or spec["decisions"], fresh=False,
                    identity_start=1000 * (1 + len(list(directory.glob("child-*.json")))), budget=budget)  # fmt: skip
    after_append = APPEND_POINTS.get(crash_point)
    commit_mode = crash_point if crash_point in ("commit_before", "commit_after", "after_acceptance_commit") else None
    if after_append or commit_mode:
        h.journal = Hook(h.real_journal, after_append, commit_mode)
    if phase == "hold":
        (directory / "holding").write_text("1")
        time.sleep(600)
        return
    if phase == "start":
        result = h.start()
    else:
        loaded = reopen(h)
        if isinstance(loaded, RunSuspended):
            result = h.resume(loaded, h.operator_export(loaded, spec["master"]))
        else:
            result = loaded
    if isinstance(result, RunSuspended):
        outcome = {"type": "suspended"}
    elif isinstance(result, RunReport):
        outcome = {"type": "report", "reason": result.final_run.termination_reason.value}
    elif isinstance(result, RunInterrupted):
        outcome = {"type": "interrupted", "code": result.code.value}
    else:
        outcome = {"type": type(result).__name__}
    index = len(list(directory.glob("child-*.json")))
    (directory / f"child-{index}.json").write_text(json.dumps(outcome))
    close(h)


if __name__ == "__main__":
    main()
