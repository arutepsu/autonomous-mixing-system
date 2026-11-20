"""Test support for Increment 5 (ADR-039): durable harnesses, a persistent fake FL host and restarts.

The fake host's fader state is persisted to a JSON file so that the simulated DAW outlives a crashed process (FL
Studio is a separate process in reality). Everything lives under a local temporary directory. Not live FL evidence.
"""

import itertools
import json
import os
from pathlib import Path

from daw_continuation_support import Continuation
from fl_device_support import FakeHost
from test_coordinator import Clock
from test_fl_runtime_recovery_controller import live_curve

from autonomous_mixing.adapters.journal.sqlite_run import SqliteRunJournal
from autonomous_mixing.core.loop.restart import reopen_daw_run, restart_facts

STORE = "run.sqlite"


class PersistedVolumes(dict):
    """The host's fader volumes, written through to ``path`` on every change (and every recorded write)."""

    def __init__(self, path: Path, initial: dict):
        self.path = Path(path)
        stored = json.loads(self.path.read_text()) if self.path.exists() else None
        super().__init__({int(k): v for k, v in (stored["volumes"] if stored else initial).items()})
        self.writes = list(stored["writes"]) if stored else []
        self._save()

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._save()

    def note_write(self, index, value):
        self.writes.append([index, value])
        self._save()

    def _save(self):
        self.path.write_text(json.dumps({"volumes": {str(k): v for k, v in self.items()}, "writes": self.writes}))


def persistent_host(directory: Path) -> FakeHost:
    host = FakeHost(normalized=0.76, curve=live_curve)
    host.volumes = PersistedVolumes(Path(directory) / "host.json", host.volumes)
    original = host.mixer.setTrackVolume

    def set_volume(index, value):
        host.volumes.note_write(index, value)
        return original(index, value)

    host.mixer.setTrackVolume = set_volume
    return host


def durable(directory: Path, *, decisions, fresh: bool, identity_start: int = 0, journal=None, budget=None,
            host=None) -> Continuation:  # fmt: skip
    """A harness over a durable run store at ``directory/run.sqlite``: created (``fresh``) or reopened."""
    path = Path(directory) / STORE

    def factory(request, initial):
        return SqliteRunJournal.create(path, request.run_id, initial) if fresh else SqliteRunJournal.open(
            path, run_id=request.run_id)  # fmt: skip

    h = Continuation(Path(directory), decisions=decisions, run_journal=factory, host=host or persistent_host(
        directory), identity_start=identity_start, write_inputs=fresh, journal=journal, budget=budget)  # fmt: skip
    # Inspection IDs are journaled by the FL adapter; every process needs its own (a driver would use unique IDs).
    ids = itertools.count(1)
    prefix = f"inspect_{os.getpid()}_{identity_start}"
    h.world.controller.next_inspect_id = lambda: f"{prefix}_{next(ids):06d}"
    return h


def close(h: Continuation) -> None:
    """End a harness as a process would: the FL journal and the run store close (their locks are released)."""
    h.close()
    h.real_journal.close()


def reopen(h: Continuation, clock=None):
    """The restart path: classify the stored run against the FL journal; a ``RunSuspended`` may then resume."""
    return reopen_daw_run(h.journal, h.world.controller, identities=h.identities, clock=clock or Clock())


def facts(h: Continuation):
    return restart_facts(h.real_journal.snapshot().value, h.world.controller)
