"""Compatibility shim (ADR-027 migration step M3): definitions moved to ``autonomous_mixing.drivers.cli``.

Removed in the class (b) step of the migration; contains no definitions of its own. It keeps the
original module execution (``python -m autonomous_mixing.cli``) by delegating to the moved ``main``.
"""

import sys

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.drivers.cli import _report_conflict as _report_conflict
from autonomous_mixing.drivers.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    sys.exit(main())
