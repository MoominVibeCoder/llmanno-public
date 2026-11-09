import json

import pandas as pd

from llmanno.core.providers.base import ChatResponse
from llmanno.harness import tree
from llmanno.harness.optimizer import (
    B2,
    B3,
    check,
    evidence,
    flip_pairs,
    flips,
    run,
    step,
    support,
)
from llmanno.harness.runtime import passages_from_ids

CODES = ["KEEP", "NONE"]
BOOK = "KEEP: keep everyone together.\nNONE: no talk move."


def data(transcripts=("T", "U")):
    rows = [
        {"ID": f"{t}-{i}", "Transkript": t, "Rolle": "Lehrkraft" if i % 2 else "Schüler",
         "Text": f"line {i}"}
        for t in transcripts for i in range(12)
    ]  # fmt: skip
    df = pd.DataFrame(rows)
    by = {f"{t} #1": [r["ID"] for r in rows if r["Transkript"] == t and r["Rolle"] == "Lehrkraft"]
          for t in transcripts}  # fmt: skip
    targets = [i for ids in by.values() for i in ids]
    return df, passages_from_ids(df, by, context=2), targets


class FakeOptimizer:
    """Antwortet auf Vorschlag und Zusammenführen mit `files`, auf die Reflexion mit Urteilen."""

    def __init__(self, files, target="KEEP -> NONE"):
        self.files, self.target, self.requests = files, target, []

    def chat(self, request):
        self.requests.append(request)
        if "verdicts" in request.schema["properties"]:
            body = {"verdicts": [], "lesson": {"cue": "c", "lesson": "l", "evidence": "e",
                                               "confidence": "low"}}  # fmt: skip
        else:
            body = {"files": [{"name": n, "content": c} for n, c in self.files.items()],
                    "notes": [{"file": "rules.md", "change": "x", "target": self.target,
                               "rationale": "y"}]}  # fmt: skip
        return ChatResponse(json.dumps(body), "m", 1, 1, 0.0, "json_schema", "stop", {})


def kinds(optimizer):
    return [
        "reflect" if "verdicts" in r.schema["properties"]
        else "compose" if "COMPOSE" in r.messages[0]["content"] else "propose"
        for r in optimizer.requests
    ]  # fmt: skip


def test_passages_from_ids_adds_context():
    _, passages, _ = data()
    assert passages[0].lines.iloc[0]["ID"] == "T-0" and passages[0].lines.iloc[-1]["ID"] == "T-11"


def test_evidence_lists_frequencies_confusions_and_cases():
    _, passages, targets = data()
    gold = dict.fromkeys(targets, "KEEP")
    pred = dict(gold) | {targets[0]: "NONE"}
    text = evidence(passages, pred, gold)
    assert "- KEEP: gold 12, coded 11" in text
    assert "- KEEP -> NONE: 1" in text
    assert "gold KEEP, coded NONE" in text and "line 1" in text


def test_check_codes_growth_and_leak():
    base = tree.from_codebook(BOOK)
    lost = tree.Harness(base.files | {"codebook.md": "KEEP: only"}, base.config)
    assert any("missing: NONE" in p for p in check(lost, base, CODES, []))
    long = tree.Harness(base.files | {"rules.md": "x" * 5000}, base.config)
    assert any("limit" in p for p in check(long, base, CODES, []))
    held_out = "so what do you think about the size of these pieces here"
    leak = tree.Harness(base.files | {"rules.md": "Example: " + held_out}, base.config)
    assert any("held-out" in p for p in check(leak, base, CODES, [held_out]))
    assert check(base, base, CODES, [held_out]) == []


def test_support_rule_needs_two_transcripts():
    _, passages, targets = data()
    gold = dict.fromkeys(targets, "KEEP")
    one = dict(gold) | {"T-1": "NONE"}  # nur in Transkript T
    both = one | {"U-1": "NONE"}
    base = tree.from_codebook(BOOK)
    note = [{"change": "c", "target": "KEEP -> NONE"}]
    assert check(base, base, CODES, [], notes=note, support_map=support(passages, one, gold))
    assert not check(base, base, CODES, [], notes=note, support_map=support(passages, both, gold))
    vague = [{"change": "c", "target": "keep vs none"}]
    assert check(base, base, CODES, [], notes=vague, support_map=support(passages, both, gold))


def test_flips_and_pairs():
    gold = {"a": "K", "b": "K", "c": "N"}
    old, new = {"a": "N", "b": "K", "c": "N"}, {"a": "K", "b": "N", "c": "K"}
    assert flips(old, new, gold) == {"fixed": 1, "broken": 2, "other": 0}
    assert flip_pairs(old, new, gold) == {"fixed K -> N": 1, "broken K -> N": 1, "broken N -> K": 1}


def step_kwargs(tmp_path, condition, optimizer, val, targets, passages):
    return dict(
        condition=condition, train=passages, gold_train=dict.fromkeys(targets, "KEEP"),
        coder=lambda h, ps: dict.fromkeys(targets, "KEEP" if "rules.md" in h.files else "NONE"),
        val_kappa=lambda h: val, optimizer=optimizer, optimizer_model="opus", codes=CODES,
        forbidden=[], library=tmp_path / "lib.jsonl", margin=0.02,
    )  # fmt: skip


def run_step(tmp_path, condition, files, val):
    _, passages, targets = data()
    base = tree.from_codebook(BOOK)
    optimizer = FakeOptimizer(files)
    result = step(
        current=base, current_pred=dict.fromkeys(targets, "NONE"), best_val=0.5, base=base,
        step_no=1, **step_kwargs(tmp_path, condition, optimizer, val, targets, passages),
    )  # fmt: skip
    return result, optimizer, tmp_path / "lib.jsonl"


def test_step_composes_reflects_and_accepts_above_margin(tmp_path):
    result, optimizer, lib = run_step(tmp_path, B3, {"rules.md": "Prefer KEEP."}, val=0.6)
    assert kinds(optimizer) == ["propose", "propose", "compose", "reflect"]
    assert result.accepted is not None and result.best_val == 0.6
    entries = [json.loads(x) for x in lib.read_text(encoding="utf-8").splitlines()]
    assert [e.get("dimension") for e in entries[:3]] == ["ablauf", "codebuch", "zusammen"]
    assert entries[0]["flips_dev_train"]["fixed"] == 12
    assert entries[3]["reflection"]["lesson"]["cue"] == "c"
    assert sum(e.get("accepted", False) for e in entries) == 1

    result, *_ = run_step(tmp_path / "b", B3, {"rules.md": "Prefer KEEP."}, val=0.51)
    assert result.accepted is None and result.best_val == 0.5


def test_unsupported_proposal_is_retried_then_rejected(tmp_path):
    _, passages, targets = data(("T",))  # Verwechslung nur in einem Transkript
    base = tree.from_codebook(BOOK)
    optimizer = FakeOptimizer({"rules.md": "Prefer KEEP."})
    result = step(
        current=base, current_pred=dict.fromkeys(targets, "NONE"), best_val=0.5, base=base,
        step_no=1, **step_kwargs(tmp_path, B2, optimizer, 0.9, targets, passages),
    )  # fmt: skip
    assert kinds(optimizer) == ["propose", "propose"]  # zweiter Versuch, keine Reflexion
    assert result.candidates[0].harness is None and result.candidates[0].problems


def test_optimizer_never_sees_val_kappa(tmp_path):
    run_step(tmp_path, B3, {"rules.md": "Prefer KEEP."}, val=0.6)
    _, optimizer, _ = run_step(tmp_path, B3, {"rules.md": "Prefer KEEP."}, val=0.6)
    task = optimizer.requests[0].messages[1]["content"]
    assert "flips_dev_train" in task and "reflection" in task and "val_kappa" not in task


def test_b2_edits_only_codebook(tmp_path):
    _, optimizer, _ = run_step(tmp_path, B2, {"codebook.md": BOOK + "\nRule."}, val=0.6)
    assert kinds(optimizer) == ["propose", "reflect"]
    schema = optimizer.requests[0].schema["properties"]["files"]["items"]["properties"]["name"]
    assert schema["enum"] == ["codebook.md"]


def test_run_keeps_accepted_version_and_its_codes(tmp_path):
    _, passages, targets = data()
    base = tree.from_codebook(BOOK)
    optimizer = FakeOptimizer({"rules.md": "Prefer KEEP."})
    saved = []
    result = run(
        base=base, base_pred=dict.fromkeys(targets, "NONE"), base_val=0.5, steps=2,
        save=saved.append, **step_kwargs(tmp_path, B3, optimizer, 0.6, targets, passages),
    )  # fmt: skip
    assert "rules.md" in result.final.files and result.best_val == 0.6
    # Im zweiten Schritt ist nichts mehr falsch: kein Vorschlag erreicht die Stützregel
    assert len(result.steps) == 2 and result.steps[1].accepted is None
    assert saved


def test_beats_noise_sign_test():
    from llmanno.harness.optimizer import beats_noise

    assert beats_noise({"fixed": 164, "broken": 40})  # Probe, Schritt 1
    assert not beats_noise({"fixed": 36, "broken": 31})  # Probe, Schritt 3: Rauschen
    assert not beats_noise({"fixed": 5, "broken": 5})
