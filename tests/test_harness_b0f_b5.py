import json
import re

import numpy as np
import pandas as pd
import pytest

from llmanno.core.providers.base import ChatResponse
from llmanno.harness import tree
from llmanno.harness.runtime import passages_from_ids, requests

pytest.importorskip("sklearn")

from llmanno.harness.assertions import Aggregator, answer, features  # noqa: E402
from llmanno.harness.memory import Memory  # noqa: E402


def frame():
    rows = []
    for t, texts in {
        "T": ["how many pieces?", "why do you think so?", "say that again", "ok next"],
        "U": ["why is that true?", "how many halves?", "everyone look here", "ok"],
    }.items():
        for k, text in enumerate(texts):
            rows.append(
                {"ID": f"{t}-s{k}", "Transkript": t, "Rolle": "Schüler", "Text": "I think 3"}
            )
            rows.append({"ID": f"{t}-{k}", "Transkript": t, "Rolle": "Lehrkraft", "Text": text})
    return pd.DataFrame(rows)


GOLD = {"T-0": "PRESS_ACC", "T-1": "PRESS_REAS", "T-2": "KEEP", "T-3": "NONE",
        "U-0": "PRESS_REAS", "U-1": "PRESS_ACC", "U-2": "KEEP", "U-3": "NONE"}  # fmt: skip


def test_memory_finds_similar_cases_from_other_transcripts():
    df = frame()
    memory = Memory.from_frame(df, [i for i in GOLD if i.startswith("U")], GOLD, k=1)
    hit = memory.search("why is it so", exclude_group="T")
    assert [c.gold for c in hit] == ["PRESS_REAS"]
    assert (
        memory.search("why is it so", exclude_group="U") == []
    )  # eigenes Transkript ausgeschlossen
    assert hit[0].before == "Schüler: I think 3"


def test_precedent_block_is_prefixed_per_passage():
    df = frame()
    memory = Memory.from_frame(df, [i for i in GOLD if i.startswith("U")], GOLD, k=1)
    passage = passages_from_ids(df, {"T #1": ["T-0", "T-1"]}, context=1)[0]
    block = memory.block(passage)
    assert block.startswith("<precedents>") and "=> PRESS_REAS" in block
    text = requests(tree.from_codebook("cb"), [passage], prefix=memory.block)[0]
    assert text.startswith("<precedents>") and "[CODE] T-1" in text


class FakeAnswerer:
    def chat(self, request):
        ids = re.findall(r"\[CODE\] (\S+)", request.messages[1]["content"])
        body = {"answers": [{"id": i, "true": ["A01"] if i.endswith("1") else []} for i in ids]}
        return ChatResponse(
            json.dumps(body), "m", 1, 1, 0.0, "json_schema", "stop", {"cost_usd": 0.02}
        )


def test_answer_collects_true_assertions_per_sentence(tmp_path):
    df = frame()
    passage = passages_from_ids(df, {"T #1": ["T-0", "T-1"]}, context=1)[0]
    result = answer(
        ["asks why", "asks how many"], [passage], FakeAnswerer(), log=tmp_path / "a.jsonl"
    )
    assert result.true == {"T-0": set(), "T-1": {"A01"}} and result.cost_usd == 0.02
    assert features(result.true, ["T-0", "T-1", "fehlt"], 2).tolist() == [[0, 0], [1, 0], [0, 0]]


def test_aggregator_learns_codes_from_assertions():
    rng = np.random.default_rng(0)
    codes = ["KEEP", "NONE", "PRESS_ACC"]
    y = [codes[k % 3] for k in range(90)]
    x = np.array([[c == "KEEP", c == "NONE", c == "PRESS_ACC"] for c in y], dtype=float)
    x += rng.normal(0, 0.1, x.shape)
    groups = [f"g{k % 6}" for k in range(90)]
    agg = Aggregator().fit(x, y, groups)
    assert agg.predict(x) == y and agg.c in agg.cv_kappa
