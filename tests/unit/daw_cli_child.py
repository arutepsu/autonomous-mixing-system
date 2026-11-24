"""Child process for the R5C C3 new-process continuation test; not collected by pytest.

Usage: ``python daw_cli_child.py <directory> <argv-json>``. ``DAW_CLI_CHILD_CRASH=after_continuation_started`` ends the
process with ``os._exit(77)`` right after the continuation marker is committed (a labelled crash point).

Runs ``main(argv)`` with only the external transports replaced by labelled doubles: the simulated FL host (fader volumes and writes persisted in ``<directory>/host.json``)
behind the reviewed v2 device script, and labelled provider replies whose call count is persisted in
``<directory>/provider_calls.json``. Exits with ``main``'s exit code.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent_runtime_support import ProviderTransport  # noqa: E402
from test_daw_cli_start import simulated  # noqa: E402

from autonomous_mixing.adapters.journal.sqlite_run import SqliteRunJournal  # noqa: E402
from autonomous_mixing.core.records.daw_correction import DawContinuationStarted  # noqa: E402
from autonomous_mixing.core.records.run_trace import TraceAppended  # noqa: E402
from autonomous_mixing.drivers import agent_runtime, daw_runtime  # noqa: E402
from autonomous_mixing.drivers.cli import main  # noqa: E402

CRASHED = 77


def run(directory: Path, argv: list[str]) -> int:
    _, factory = simulated(directory)
    daw_runtime.WinMMTransport = factory
    agent_runtime.WinMMTransport = factory
    counter = directory / "provider_calls.json"
    provider = ProviderTransport()
    original = provider.send

    def counted(body, *, timeout_seconds):
        calls = json.loads(counter.read_text()) if counter.exists() else 0
        counter.write_text(json.dumps(calls + 1))
        return original(body, timeout_seconds=timeout_seconds)

    provider.send = counted
    agent_runtime.ClaudeMessagesTransport = lambda config, api_key: provider
    if os.environ.get("DAW_CLI_CHILD_CRASH") == "after_continuation_started":
        append = SqliteRunJournal.append

        def crash_after_marker(self, event):  # labelled crash point: no cleanup, no closing, as after a crash
            result = append(self, event)
            if isinstance(event.payload, DawContinuationStarted) and isinstance(result, TraceAppended):
                os._exit(CRASHED)
            return result

        SqliteRunJournal.append = crash_after_marker
    return main(argv)


if __name__ == "__main__":
    sys.exit(run(Path(sys.argv[1]), json.loads(sys.argv[2])))
