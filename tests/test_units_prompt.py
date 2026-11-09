import pytest

from llmanno.core.config import CodebookConfig, UnitSettings
from llmanno.core.prompt import PromptSpec, estimate_tokens, render, render_system, repair_message
from llmanno.core.store import Unit
from llmanno.core.units import build_items

UNITS = [
    Unit("1", 2, "T1", 1, "L", "Was ist ein Bruch?", {"Frage": "F1"}),
    Unit("2", 3, "T1", 2, "S", "Ein Teil vom Ganzen."),
    Unit("3", 4, "T2", 1, "L", "Gut."),
    Unit("4", 5, "T1", 3, "L", "Genau."),
]
CODEBOOK = CodebookConfig.model_validate(
    {
        "source_name": "cb.txt", "sha256": "x", "encoding": "utf-8",
        "codes": [{"id": "K1", "label": "Frage"}, {"id": "K2", "label": "Antwort"}, {"id": "K3"}],
    }
)  # fmt: skip


def spec(**overrides):
    fields = dict(
        language="de", mode="single", k=3, unit_mode="context", has_meta=True,
        codebook_text="### K1 — Frage\n", codebook=CODEBOOK, max_signal_words=3,
        max_rationale_words=40, permute_codes=True,
    )  # fmt: skip
    return PromptSpec(**{**fields, **overrides})


def test_isolated_items_keep_row_order():
    items = build_items(UNITS, UnitSettings(mode="isolated"))
    assert [i.item_id for i in items] == ["1", "2", "3", "4"]
    assert items[0].target == "Was ist ein Bruch?" and items[0].before == ()


def test_context_window_stays_within_group():
    items = {
        i.item_id: i
        for i in build_items(UNITS, UnitSettings(mode="context", n_before=2, n_after=1))
    }
    assert [ln.unit_id for ln in items["4"].before] == ["1", "2"]
    assert items["4"].after == ()
    assert [ln.unit_id for ln in items["1"].after] == ["2"]
    assert items["3"].before == () and items["3"].after == ()  # T2 hat nur eine Zeile


def test_holistic_items_per_group():
    items = build_items(UNITS, UnitSettings(mode="holistic"))
    assert [i.item_id for i in items] == ["T1", "T2"]
    assert items[0].target == "Was ist ein Bruch?\nEin Teil vom Ganzen.\nGenau."
    assert items[0].meta == {"Frage": "F1"}


def test_holistic_requires_groups():
    with pytest.raises(ValueError, match="Gruppen-Spalte"):
        build_items([Unit("1", 2, None, 1, None, "x")], UnitSettings(mode="holistic"))


def test_system_prompt_contains_codebook_verbatim_and_permutes_codes():
    first, second = render_system(spec(), seed=1), render_system(spec(), seed=2)
    assert "<codebuch>\n### K1 — Frage\n</codebuch>" in first
    assert first.count("- KEIN_CODE:") == 1
    orders = {
        tuple(line for line in text.splitlines() if line.startswith("- K") and "KEIN" not in line)
        for text in (render_system(spec(), seed=s) for s in range(10))
    }
    assert len(orders) > 1  # Permutation greift
    assert render_system(spec(), seed=1) == first  # und ist reproduzierbar
    assert first.splitlines()[-1] != "" and '"candidates"' in first
    assert first != second or len(orders) > 1


def test_system_prompt_without_permutation_keeps_codebook_order():
    text = render_system(spec(permute_codes=False), seed=5)
    assert text.index("- K1: Frage") < text.index("- K2: Antwort") < text.index("- K3\n")


def test_mode_specific_rules():
    single = render_system(spec(k=1), seed=1)
    assert "Nenne den am besten passenden Code." in single
    multi = render_system(spec(mode="multi", k=2), seed=1)
    assert "höchstens 2" in multi and "KEIN_CODE steht immer allein" in multi
    english = render_system(spec(language="en", unit_mode="holistic"), seed=1)
    assert "<codebook>" in english and "Read the unit." in english
    assert "Kontext dient" not in render_system(spec(unit_mode="isolated"), seed=1)


def test_user_message_with_context_meta_and_speaker():
    items = {
        i.item_id: i
        for i in build_items(UNITS, UnitSettings(mode="context", n_before=1, n_after=1))
    }
    user = render(spec(), items["2"], seed=1).user
    assert user == (
        "<kontext_davor>\n[1] L: Was ist ein Bruch?\n</kontext_davor>\n"
        '<zieltext id="2" sprecher="S">\nEin Teil vom Ganzen.\n</zieltext>\n'
        "<kontext_danach>\n[4] L: Genau.\n</kontext_danach>"
    )
    assert render(spec(), items["1"], seed=1).user.startswith(
        "<metadaten>\nFrage: F1\n</metadaten>"
    )


def test_user_message_holistic():
    [t1, _] = build_items(UNITS, UnitSettings(mode="holistic"))
    user = render(spec(unit_mode="holistic"), t1, seed=1).user
    assert '<einheit id="T1">\n[1] L: Was ist ein Bruch?\n[2] S: Ein Teil vom Ganzen.' in user


def test_repair_message_and_token_estimate():
    assert "der Einheit" in repair_message("de", "evidence", "„x“", "holistic")
    assert "the target text" in repair_message("en", "evidence", "x", "context")
    assert estimate_tokens("abcdefg") == 3
