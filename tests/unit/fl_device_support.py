"""Test-only loading of FL device scripts with a fake host; never imports FL Studio modules.

Device scripts import their protocol by a plain module name, as FL Studio does from the script folder. The loader
installs that module in ``sys.modules`` only for the duration of loading and restores the previous state.
"""

import importlib.util
import math
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V1_DIR = ROOT / "scripts" / "fl_studio"
V2_DIR = ROOT / "device" / "fl_studio" / "thesis_bridge_v2"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _plain_name(name, module):
    previous = sys.modules.get(name, None)
    present = name in sys.modules
    sys.modules[name] = module
    try:
        yield
    finally:
        if present:
            sys.modules[name] = previous
        else:
            del sys.modules[name]


def load_device(version):
    """(device module, its protocol module), loaded in isolation; sys.modules is restored afterwards."""
    if version == "v1":
        protocol = _load_module("fl_v1_protocol_under_test", V1_DIR / "bridge_protocol.py")
        with _plain_name("bridge_protocol", protocol):
            device = _load_module("fl_v1_device_under_test", V1_DIR / "device_bridge.py")
    else:
        protocol = _load_module("fl_v2_protocol_under_test", V2_DIR / "thesis_bridge_protocol.py")
        with _plain_name("thesis_bridge_protocol", protocol):
            device = _load_module("fl_v2_device_under_test", V2_DIR / "device_thesis_bridge.py")
    return device, protocol


def h_db(v):
    return 20.0 * math.log10((math.exp(1.25 * v * math.log(11.0)) - 1.0) / 10.0)


class FakeHost:
    """FL host stand-in whose fader follows the calibrated curve; records every call."""

    def __init__(self, normalized=0.7257362319041692, build="Producer Edition v26.1.7 [build 5654]", api=45,
                 title="Thesis Bass Bridge Test", names=("Kick", "Bass"), curve=h_db):  # fmt: skip
        host = self
        self.calls, self.writes = [], []
        self.curve, self.playing, self.safe = curve, False, True
        self.volumes = {1: 0.8, 2: normalized}
        self.fail_after_set = None
        self.land_offset = 0.0

        class General:
            def getProjectTitle(self):
                host.calls.append("getProjectTitle")
                return title

            def getVersion(self):
                host.calls.append("getVersion")
                return api

            def safeToEdit(self):
                host.calls.append("safeToEdit")
                return host.safe

        class Mixer:
            def trackCount(self):
                host.calls.append("trackCount")
                return len(names) + 2

            def getTrackName(self, index):
                host.calls.append("getTrackName")
                return names[index - 1]

            def getTrackVolume(self, index, mode=0):
                host.calls.append("getTrackVolume")
                value = host.volumes[index]
                return value if mode == 0 else host.curve(value)

            def setTrackVolume(self, index, value):
                host.calls.append("setTrackVolume")
                host.writes.append((index, value))
                host.volumes[index] = value + host.land_offset
                if host.fail_after_set is not None:
                    raise host.fail_after_set

        class Transport:
            def isPlaying(self):
                host.calls.append("isPlaying")
                return host.playing

        class UI:
            def getVersion(self, mode=0):
                host.calls.append("ui.getVersion")
                return build

        self.general, self.mixer, self.transport, self.ui = General(), Mixer(), Transport(), UI()

    def reading(self, index=2):
        value = self.volumes[index]
        return {"normalized": value, "db": self.curve(value)}
