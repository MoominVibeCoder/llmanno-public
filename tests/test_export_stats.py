import pyreadr
import pyreadstat
import pytest
from test_evaluation import CODES, ROWS, WRONG, make_project

from llmanno.core.export.stats import _variable_name, export_stats


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path)
    yield project
    project.close()


def test_spss_export_has_numeric_codes_with_value_labels(project, tmp_path):
    path = tmp_path / "e.sav"
    export_stats(project, path, "sav")
    frame, meta = pyreadstat.read_sav(str(path))
    assert len(frame) == len(ROWS)
    labels = meta.variable_value_labels["llm_code"]
    assert labels[1].startswith("K1")
    first = frame.iloc[0]
    assert labels[int(first["llm_code"])].startswith(CODES[1] if "U00" in WRONG else CODES[0])
    assert {"gold_A", "gold_B", "llm_p_sc", "llm_flags", "Transkript"} <= set(frame.columns)
    assert meta.column_names_to_labels["llm_p_sc"].startswith("LLM: Self-Consistency")
    assert meta.variable_value_labels["gold_A"] == labels


def test_stata_export_respects_name_limits(project, tmp_path):
    path = tmp_path / "e.dta"
    export_stats(project, path, "dta")
    frame, meta = pyreadstat.read_dta(str(path))
    assert all(len(c) <= 32 for c in frame.columns)
    assert "llm_code" in meta.variable_value_labels


def test_r_export_with_factor_script(project, tmp_path):
    path = tmp_path / "e.rds"
    export_stats(project, path, "rds")
    frame = pyreadr.read_r(str(path))[None]
    assert len(frame) == len(ROWS) and frame["llm_code"].iloc[0] in CODES
    script = (tmp_path / "e.R").read_text(encoding="utf-8")
    assert 'readRDS("e.rds")' in script
    assert 'stufen <- c("K1", "K2", "K3", "KEIN_CODE")' in script
    assert '"llm_code"' in script and '"gold_A"' in script


def test_variable_names_are_valid_and_unique():
    taken = set()
    assert _variable_name("Coder A", taken, 64) == "Coder_A"
    assert _variable_name("coder a", taken, 64) == "coder_a_2"
    assert _variable_name("1. Frage", taken, 64) == "v_1_Frage"
    assert _variable_name("Ärger über Maß", taken, 64) == "Aerger_ueber_Mass"
    assert len(_variable_name("x" * 50, taken, 32)) == 32


def test_dsl_columns_follow_the_review_design(project, tmp_path):
    from test_evaluation import TRUTH

    from llmanno.core.review import RANDOM, add_review, build_queue

    project.update(review=project.config.review.model_copy(update={"random_share": 0.25}))
    queue = build_queue(project)
    reviewed = [e for e in queue if RANDOM in e.reasons][:10]
    for entry in reviewed:
        add_review(project, entry.item_id, "Ana", "blind", [TRUTH[entry.item_id]])
    path = tmp_path / "d.rds"
    export_stats(project, path, "rds")
    frame = pyreadr.read_r(str(path))[None].set_index("item_id")
    assert frame["dsl_labeled"].sum() == 10
    assert set(frame["dsl_sample_prob"].round(4)) <= {0.25, 1.0}
    first = reviewed[0].item_id
    code = TRUTH[first]
    assert frame.loc[first, f"dsl_review_{code}"] == 1
    assert frame[f"dsl_review_{code}"].isna().sum() == len(ROWS) - 10
    assert set(frame["dsl_llm_K1"].dropna()) <= {0, 1}
    script = (tmp_path / "d.R").read_text(encoding="utf-8")
    assert 'labeled = "dsl_labeled"' in script and "library(dsl)" in script


def test_no_dsl_columns_without_queue(project, tmp_path):
    path = tmp_path / "e.sav"
    export_stats(project, path, "sav")
    frame, _ = pyreadstat.read_sav(str(path))
    assert not any(c.startswith("dsl_") for c in frame.columns)
