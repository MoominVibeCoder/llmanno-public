import json

import pytest

from llmanno.core.evidence import locate
from llmanno.core.schema import AnswerError, parse_answer, response_schema

ALLOWED = ["K1", "K2", "K3", "KEIN_CODE"]


def answer(*candidates, rationale="weil"):
    return json.dumps(
        {
            "rationale": rationale,
            "candidates": [
                {"evidence": ev, "code": code, "confidence": conf} for code, conf, ev in candidates
            ],
        }
    )


def parse(text, mode="single", k=3, max_sw=3):
    return parse_answer(
        text, allowed=ALLOWED, abstain_id="KEIN_CODE", mode=mode, k=k, max_signal_words=max_sw
    )


def test_schema_field_order_puts_rationale_and_evidence_first():
    schema = response_schema(ALLOWED)
    assert list(schema["properties"]) == ["rationale", "candidates"]
    item = schema["properties"]["candidates"]["items"]
    assert list(item["properties"]) == ["evidence", "code", "confidence"]
    assert item["properties"]["code"]["enum"] == ALLOWED


def test_valid_single_answer():
    parsed = parse(answer(("K1", 70, ["Bruch"]), ("K2", 20, ["Teil"])))
    assert [(c.code, c.confidence) for c in parsed.candidates] == [("K1", 70), ("K2", 20)]
    assert parsed.flags == [] and parsed.rationale == "weil"


def test_json_inside_code_fence_is_accepted():
    text = "Hier:\n```json\n" + answer(("K1", 90, ["x"])) + "\n```"
    assert parse(text).candidates[0].code == "K1"


@pytest.mark.parametrize(
    "text, kind",
    [
        ("kein json", "json"),
        ('{"rationale": "x"}', "json"),
        (answer(("K9", 50, ["x"])), "codes"),
        (answer(("K1", 50, [" "])), "rules"),
        (answer(), "rules"),
    ],
)
def test_unusable_answers_raise_for_repair(text, kind):
    with pytest.raises(AnswerError) as info:
        parse(text)
    assert info.value.kind == kind


def test_abstain_needs_no_evidence_but_must_stand_alone_in_multi():
    assert parse(answer(("KEIN_CODE", 80, []))).candidates[0].code == "KEIN_CODE"
    with pytest.raises(AnswerError, match="zusammen"):
        parse(answer(("K1", 80, ["x"]), ("KEIN_CODE", 30, [])), mode="multi")


def test_lenient_corrections_are_flagged():
    parsed = parse(
        answer(
            ("K1", 150, ["a", "b", "c", "d"]),
            ("K1", 10, ["x"]),
            ("K2", 60, ["y"]),
            ("K3", 5, ["z"]),
        ),
        k=2,
    )
    assert [c.code for c in parsed.candidates] == ["K1", "K2"]
    assert parsed.candidates[0].evidence == ["a", "b", "c"]
    assert set(parsed.flags) == {
        "konfidenz_begrenzt", "zu_viele_signalwoerter", "doppelter_code", "zu_viele_codes",
        "konfidenz_normalisiert",
    }  # fmt: skip
    assert sum(c.confidence for c in parsed.candidates) == pytest.approx(100)


def test_multi_confidences_are_not_normalized():
    parsed = parse(answer(("K1", 90, ["x"]), ("K2", 80, ["y"])), mode="multi")
    assert [c.confidence for c in parsed.candidates] == [90, 80]


TEXT = "Na ja, ein „Teil“ vom  Ganzen – würde ich sagen. Straße, Teil."


def test_locate_exact_with_ambiguity_and_stripped_quotes():
    hit = locate('"vom  Ganzen"', TEXT)
    assert (hit.status, TEXT[hit.start : hit.end], hit.ambiguous) == ("exact", "vom  Ganzen", False)
    assert locate("Teil", TEXT).ambiguous


def test_locate_normalized_maps_back_to_original_offsets():
    hit = locate('ein "teil" vom ganzen - würde', TEXT)
    assert hit.status == "normalized"
    assert TEXT[hit.start : hit.end] == "ein „Teil“ vom  Ganzen – würde"
    hit = locate("STRASSE", TEXT)
    assert (hit.status, TEXT[hit.start : hit.end]) == ("normalized", "Straße")


def test_locate_fuzzy_and_missing():
    hit = locate("vom Ganzn – würde", TEXT)  # ein Tippfehler: Score ~94
    assert hit.status == "fuzzy" and "Ganzen" in TEXT[hit.start : hit.end]
    assert locate("vom Ganzn würde", TEXT).status == "missing"  # zwei Abweichungen: Score 80
    assert locate("Quadratwurzel", TEXT).status == "missing"
    assert locate("xy", TEXT).status == "missing"  # zu kurz für unscharfe Suche
    assert locate("viel länger als der ganze Text " * 3, "kurz").status == "missing"
    assert locate(" ... ", TEXT).status == "missing"
