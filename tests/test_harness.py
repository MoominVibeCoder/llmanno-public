import json
import re

import pandas as pd
import pytest

from llmanno.core.providers.base import ChatResponse
from llmanno.harness import tree
from llmanno.harness.gold import GoldGuard
from llmanno.harness.runtime import code, draw_passages

CODES = ["KEEP", "NONE"]


def transcript(groups=2, lines=30):
    rows = []
    for g in range(groups):
        for i in range(lines):
            role = "Lehrkraft" if i % 3 else "Schüler"
            rows.append(
                {"ID": f"G{g}-{i:03d}", "Transkript": f"T{g}", "Rolle": role, "Text": f"s{i}"}
            )
    return pd.DataFrame(rows)


class FakeProvider:
    """Kodiert jede [CODE]-Zeile mit KEEP; merkt sich die Anfragen."""

    def __init__(self, text=None):
        self.requests, self.text = [], text

    def chat(self, request):
        self.requests.append(request)
        ids = re.findall(r"\[CODE\] (\S+)", request.messages[1]["content"])
        text = self.text or json.dumps({"codes": [{"id": i, "code": "KEEP"} for i in ids]})
        return ChatResponse(text, "m", 1, 1, 0.0, "json_schema", "stop", {"cost_usd": 0.01})


def test_tree_roundtrip_hash_and_diff(tmp_path):
    h = tree.from_codebook("KEEP: keep everyone together")
    loaded = tree.load(tree.save(h, tmp_path / "v0"))
    assert loaded.sha == h.sha and loaded.files == h.files
    edited = tree.Harness(h.files | {"rules.md": "Prefer NONE."}, h.config, parent=h.sha)
    assert edited.sha != h.sha
    assert "+Prefer NONE." in tree.diff(h, edited)
    assert "<rules>\nPrefer NONE.\n</rules>" in edited.system_prompt(CODES)
    tree.save(h, tmp_path / "v0")  # Datei, die es in der Fassung nicht mehr gibt, fällt weg
    assert not (tmp_path / "v0" / "rules.md").exists()


def test_passages_do_not_overlap_and_are_reproducible():
    df = transcript(lines=60)
    a = draw_passages(df, target_role="Lehrkraft", n=10, per_group=2, seed=1, context=3)
    b = draw_passages(df, target_role="Lehrkraft", n=10, per_group=2, seed=1, context=3)
    assert [p.targets for p in a] == [p.targets for p in b]
    for g in ("T0", "T1"):
        sets = [set(p.targets) for p in a if p.name.startswith(g)]
        assert len(sets) == 2 and not sets[0] & sets[1]


@pytest.mark.parametrize("unit,calls", [("passage", 2), ("sentence", 20)])
def test_code_by_unit(tmp_path, unit, calls):
    df = transcript()
    passages = draw_passages(df, target_role="Lehrkraft", n=10, per_group=1, seed=0, context=3)
    h = tree.from_codebook("cb", unit=unit)
    provider = FakeProvider()
    result = code(h, passages, CODES, provider, log=tmp_path / "log.jsonl", workers=2)
    assert result.requests == calls == len(provider.requests)
    assert set(result.codes.values()) == {"KEEP"} and len(result.codes) == 20
    assert result.cost_usd == pytest.approx(0.01 * calls)
    assert provider.requests[0].reasoning_effort == "medium"
    entries = (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(entries) == calls and json.loads(entries[0])["harness"] == h.sha


def test_invalid_answers_stay_missing():
    df = transcript(groups=1)
    passages = draw_passages(df, target_role="Lehrkraft", n=5, per_group=1, seed=0, context=0)
    bad = json.dumps({"codes": [{"id": "erfunden", "code": "KEEP"}]})
    result = code(tree.from_codebook("cb"), passages, CODES, FakeProvider(bad))
    assert set(result.codes.values()) == {None}


def test_gold_guard_roles(tmp_path):
    gold = {"a": "KEEP", "b": "NONE", "c": "KEEP", "d": "NONE"}
    split = {"a": "dev_train", "b": "dev_val", "c": "dev_val", "d": "test"}
    log = tmp_path / "gold.jsonl"
    guard = GoldGuard(gold, split, log, max_val_looks=2)
    assert guard.train_labels(["a"]) == {"a": "KEEP"}
    with pytest.raises(PermissionError, match="dev_val"):
        guard.train_labels(["a", "b"])
    assert guard.val_score({"b": "NONE", "c": "KEEP"}, sha="v1") == pytest.approx(1.0)
    guard.val_score({}, sha="v2")
    with pytest.raises(PermissionError, match="Blicke"):
        guard.val_score({}, sha="v3")
    assert GoldGuard(gold, split, log, max_val_looks=2).val_looks == 2  # über Neustarts hinweg
    with pytest.raises(PermissionError, match="Freeze"):
        guard.test_score({"d": "NONE"}, sha="v1")
    guard.frozen = True
    guard.test_score({"d": "NONE"}, sha="v1")
    with pytest.raises(PermissionError, match="schon"):
        guard.test_score({"d": "NONE"}, sha="v1")


def test_chat_retries_transient_errors():
    from llmanno.core.providers.base import ProviderError
    from llmanno.harness.runtime import chat

    class Flaky(FakeProvider):
        def __init__(self):
            super().__init__()
            self.fails = 2

        def chat(self, request):
            if self.fails:
                self.fails -= 1
                raise ProviderError("Overloaded", retryable=True)
            return super().chat(request)

    provider, waits = Flaky(), []
    request = tree.from_codebook("cb")  # nur als Platzhalter für die Anfrage unten
    from llmanno.core.providers.base import ChatRequest

    req = ChatRequest("m", [{"role": "system", "content": request.system_prompt(CODES)},
                            {"role": "user", "content": "[CODE] a x"}], None, 10)  # fmt: skip
    assert chat(provider, req, sleep=waits.append).text
    assert waits == [5, 10]


def test_hints_are_attached_to_target_lines_only():
    from llmanno.harness.runtime import requests

    df = transcript(groups=1)
    passage = draw_passages(df, target_role="Lehrkraft", n=2, per_group=1, seed=0, context=2)[0]
    first = passage.targets[0]
    for unit in ("passage", "sentence"):
        text = requests(tree.from_codebook("cb", unit=unit), [passage], hints={first: "A01 yes"})[0]
        assert f"[CODE] {first}" in text and "(checks: A01 yes)" in text
    texts = requests(tree.from_codebook("cb", unit="sentence"), [passage], hints={first: "A01 yes"})
    assert "(checks:" not in texts[1]  # nur am eigenen Zielsatz
