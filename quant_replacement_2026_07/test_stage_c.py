import numpy as np
import pandas as pd

from run_stage_c import FEATURES, design_matrix, fit_ridge, predict


def synthetic_teacher(rows=600):
    index = pd.date_range("2020-01-01", periods=rows, freq="h", tz="UTC")
    frame = pd.DataFrame({name: np.sin(np.arange(rows) / (i + 3))
                          for i, name in enumerate(FEATURES)}, index=index)
    frame["sym"] = np.resize(np.array(["NQ", "ES", "YM"]), rows)
    frame["y"] = 0.2 * frame["t120"] - 0.1 * frame["vol_ratio"]
    frame["exit_time"] = index
    return frame


def test_ridge_prediction_is_invariant_to_future_teacher_outcome():
    teachers = synthetic_teacher()
    cutoff = teachers.index[550]
    train = teachers[teachers["exit_time"] < cutoff]
    decision = teachers.iloc[[550]].copy()
    before = predict(fit_ridge(train, 10.0), decision)
    changed = teachers.copy()
    changed.loc[changed["exit_time"] >= cutoff, "y"] = 1_000_000.0
    changed_train = changed[changed["exit_time"] < cutoff]
    after = predict(fit_ridge(changed_train, 10.0), decision)
    np.testing.assert_allclose(before[0], after[0])
    np.testing.assert_allclose(before[1], after[1])


def test_design_matrix_uses_training_standardization_only():
    teachers = synthetic_teacher()
    train = teachers.iloc[:500]
    future = teachers.iloc[500:].copy()
    _, means, scales = design_matrix(train)
    first, _, _ = design_matrix(future, means, scales)
    future.loc[future.index[1]:, list(FEATURES)] *= 1_000_000.0
    second, _, _ = design_matrix(future, means, scales)
    np.testing.assert_allclose(first[0], second[0])


def test_ablation_model_retains_requested_feature_identity():
    teachers = synthetic_teacher()
    retained = tuple(name for name in FEATURES if name != "hour_sin")
    model = fit_ridge(teachers, 10.0, feature_names=retained)
    assert model[-1] == retained
    mean, evidence = predict(model, teachers.iloc[:2])
    assert np.isfinite(mean).all()
    assert np.isfinite(evidence).all()
