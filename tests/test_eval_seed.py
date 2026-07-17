import math
import numpy as np
from refrain.primitive_impls import PercentileImpl


def test_ingest_appends_without_computing_and_skips_nonfinite():
    p = PercentileImpl(target_pct=70.0, window_ms=1000.0, sample_rate_hz=10.0)  # 10 samples
    p.ingest(np.array([1.0, 2.0, np.nan, 3.0, np.inf, 4.0]))
    st = p.export_state()
    assert st["n_eff"] == 4                    # nan/inf skipped, not counted
    assert st["value"] == np.percentile([1.0, 2.0, 3.0, 4.0], 70.0)
