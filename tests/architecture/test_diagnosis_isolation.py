"""R2A diagnosis isolation: pure stages and driver load no audio/provider or unrelated stage."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "autonomous_mixing.core.records.masking_diagnosis",
        "autonomous_mixing.core.diagnosis.masking",
        "autonomous_mixing.drivers.diagnosis_runtime",
    ],
)
def test_diagnosis_import_is_isolated_from_audio_provider_and_other_stages(module):
    script = f"import sys\nimport {module}\nprint(chr(10).join(sorted(sys.modules)))"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    modules = result.stdout.split()
    forbidden = (
        "numpy",
        "scipy",
        "soundfile",
        "anthropic",
        "httpx2",
        "autonomous_mixing.adapters",
        "autonomous_mixing.core.loop",
        "autonomous_mixing.core.admission",
        "autonomous_mixing.core.verification",
        "autonomous_mixing.drivers.cli",
        "autonomous_mixing.drivers.daw_runtime",
    )
    assert not [m for m in modules if any(m == p or m.startswith(p + ".") for p in forbidden)]
