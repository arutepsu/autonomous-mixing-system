"""Exact runtime FL adapter boundaries and import isolation; no native device calls."""

import json
import subprocess
import sys

import pytest
from test_adapters import _edges

BASE = "autonomous_mixing.adapters.fl_studio"
CORE = "autonomous_mixing.core"
EDGES = {
    "protocol": set(),
    "transport": {f"{BASE}.protocol"},
    "exchange": {f"{BASE}.protocol", f"{BASE}.transport"},
    "profile": {
        f"{BASE}.protocol",
        f"{CORE}.records.daw_control",
        f"{CORE}.records.identifiers",
        f"{CORE}.records.invariants",
    },
    # recovery plan 2026-11-18: serialization and journal gain the recovery and budget records (exact additions)
    "serialization": {
        f"{CORE}.records.budgets",
        f"{CORE}.records.daw_binding",
        f"{CORE}.records.daw_control",
        f"{CORE}.records.daw_recovery",
        f"{CORE}.records.identifiers",  # export plan 2026-11-18: journaled binding, profile and execution loaders
        # review fix 2026-11-19: the exact journaled apply request is rebuilt for export anchors
        f"{CORE}.records.actions",
        f"{CORE}.records.admission",
        f"{CORE}.records.daw",
    },
    "journal": {
        f"{BASE}.serialization",
        f"{CORE}.records.budgets",
        f"{CORE}.records.daw_binding",
        f"{CORE}.records.daw_control",
        f"{CORE}.records.daw_recovery",
        f"{CORE}.records.identifiers",
    },
    "controller": {
        f"{BASE}.{name}" for name in ("protocol", "exchange", "journal", "profile", "serialization", "transport")
    }
    | {
        f"{CORE}.ports.daw",
        f"{CORE}.ports.daw_recovery",
        f"{CORE}.records.daw_binding",
        f"{CORE}.records.daw_control",
        f"{CORE}.records.daw_export",  # export plan 2026-11-18: state anchors and continuity snapshots
        f"{CORE}.records.daw_recovery",
        f"{CORE}.records.invariants",
        f"{CORE}.records.results",
        f"{CORE}.records.run_restart",  # Increment 5 (ADR-039): the latest state-establishing event
    },
}


@pytest.mark.parametrize("module", EDGES)
def test_fl_adapter_exact_edges_and_standard_library_only(module):
    assert _edges(f"{BASE}.{module}") == (EDGES[module], set())


def test_fl_component_module_inventory_is_complete():
    from test_adapters import MODULES

    assert {name for name in MODULES if name.startswith(BASE + ".")} == {f"{BASE}.{module}" for module in EDGES}


@pytest.mark.parametrize("module", ["", ".controller"])
def test_import_does_not_open_database_or_load_native_devices(module):
    script = f"""
import sys,sqlite3,ctypes,importlib,json
def forbidden(*args, **kwargs): raise RuntimeError('import performed I/O')
sqlite3.connect = forbidden
ctypes.WinDLL = forbidden
importlib.import_module({(BASE + module)!r})
loaded=set(sys.modules)
assert not any(name.split('.')[0] in ('numpy','scipy','soundfile','anthropic') for name in loaded)
assert not any(name.startswith(('autonomous_mixing.core.loop','autonomous_mixing.core.admission','scripts.')) for name in loaded)
if not {bool(module)!r}:
    assert {{name for name in loaded if name.startswith({BASE!r})}} == {{{BASE!r}}}
print(json.dumps(True))
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is True
