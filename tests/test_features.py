import numpy as np

from autonomous_mixing.adapters.dsp_analysis.features import coactivity_proxy, peak_dbfs, rms_dbfs


def test_silence_levels_are_null():
    assert rms_dbfs(np.zeros((4096, 1))) is None
    assert peak_dbfs(np.zeros((4096, 1))) is None


def test_unit_peak():
    assert peak_dbfs(np.array([[1.0], [-1.0]])) == 0.0


def test_coactivity_no_activity():
    assert coactivity_proxy(np.zeros((10, 8)), np.ones((10, 8))) == 0.0
