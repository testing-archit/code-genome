import numpy as np
import pytest
from code_genome_ml import defect
from code_genome_ml.defect import _forest, _shap_contributions, train_defect_model
from test_models import synthetic_repository


def _fix_shas() -> tuple[object, set[str]]:
    inputs = synthetic_repository()
    fixes = {
        commit.sha
        for commit in inputs.commits
        if commit.message.split(": ")[-1].startswith(("fix ", "resolve ", "handle "))
    }
    return inputs, fixes


def test_treeshap_values_add_up_to_the_forest_probability() -> None:
    pytest.importorskip("shap")
    rng = np.random.default_rng(7)
    x = rng.normal(size=(80, 4))
    y = (x[:, 0] + 0.5 * x[:, 1] > 0).astype(int)
    model = _forest().fit(x, y)
    values = _shap_contributions(model, x[:5], "random_forest")
    assert values is not None and values.shape == (5, 4)
    import shap

    expected = np.asarray(shap.TreeExplainer(model).expected_value).reshape(-1)[-1]
    probabilities = model.predict_proba(x[:5])[:, 1]
    assert np.allclose(values.sum(axis=1) + expected, probabilities, atol=1e-6)
    assert abs(values[:, 0]).mean() > abs(values[:, 3]).mean()  # the informative feature


def test_tree_champions_use_shap_when_installed_and_say_so() -> None:
    pytest.importorskip("shap")
    inputs, fixes = _fix_shas()
    result = train_defect_model(inputs.commits, inputs.changes, fixes, inputs.files, inputs.imports)  # type: ignore[attr-defined]
    if result.champion == "logistic_regression":
        assert result.contribution_unit == "log-odds" and "coefficient" in (
            result.contribution_method or ""
        )
    else:
        assert "TreeSHAP" in (result.contribution_method or "")


def test_without_shap_trees_fall_back_to_reset_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defect, "_shap_contributions", lambda *args: None)
    monkeypatch.setattr(defect, "CANDIDATES", ("random_forest",))
    inputs, fixes = _fix_shas()
    result = train_defect_model(inputs.commits, inputs.changes, fixes, inputs.files, inputs.imports)  # type: ignore[attr-defined]
    assert result.champion == "random_forest"
    assert "reset-to-typical" in (result.contribution_method or "")
