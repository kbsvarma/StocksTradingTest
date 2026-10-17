"""Fail-closed validation of the audit and both extension ledgers."""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent


def main() -> None:
    audit = pd.read_csv(BASE / "audit_results.csv").set_index("check")["value"]
    assert int(audit["cached_sessions"]) == 2050
    assert audit["cached_last_day"] == "2026-07-29"
    assert int(audit["reproduced_trades_all"]) == 2030
    assert int(audit["rows_only_one_side"]) == 0
    assert float(audit["max_abs_saved_net_difference"]) < 1e-9
    assert np.isclose(float(audit["headline_mean_usd"]), -12.98796802054097)
    assert int(audit["exact_mined_window_n"]) == 29

    simple_manifest = pd.read_csv(BASE / "variant_manifest.csv")
    simple = pd.read_csv(BASE / "variant_is_results.csv")
    assert len(simple_manifest) == 180
    assert len(simple) == 180
    assert simple_manifest["cell_id"].is_unique
    assert simple["cell_id"].is_unique
    assert set(simple_manifest["cell_id"]) == set(simple["cell_id"])
    assert not simple["is_pass"].any()
    assert simple["t"].max() < 1.5

    model_manifest = pd.read_csv(BASE / "model_manifest.csv")
    model = pd.read_csv(BASE / "model_oof_results.csv")
    assert len(model_manifest) == 135
    assert len(model) == 135
    assert model_manifest["cell_id"].is_unique
    assert model["cell_id"].is_unique
    assert set(model_manifest["cell_id"]) == set(model["cell_id"])
    assert not model["oof_pass"].any()
    assert model["t"].max() < 1.5

    assert not (BASE / "variant_holdout_results.csv").read_text().strip()
    assert not (BASE / "model_holdout_result.csv").read_text().strip()
    print("PASS: audit exact; 180 rule cells and 135 model cells fully ledgered")


if __name__ == "__main__":
    main()
