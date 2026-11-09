"""Demo-Projekt: das Beispiel (200 synthetische Gesprächszüge) mit simulierten LLM-Antworten.

Zum Anschauen ohne eigene Daten und ohne Modell. Die Antworten erzeugt ein Simulator aus der
Kodierung „Coder A“ mit eingestreuten Fehlern; Anbieter und Modelle heißen „Demo-Simulator“,
damit niemand sie für echte LLM-Ergebnisse hält. Der Simulator läuft durch die normale Engine:
Aufrufe, Aggregation, Konfidenzen und Signalwörter sind wie bei einem echten Lauf verarbeitet.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from llmanno.core.codebook import suggest_auto
from llmanno.core.config import Code, ColumnMapping, ModelConfig, ProviderConfig, UnitSettings
from llmanno.core.engine import Engine
from llmanno.core.i18n import t
from llmanno.core.project import Project
from llmanno.core.providers.base import ChatRequest, ChatResponse, ModelInfo

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"
DEMO_PROVIDER = ProviderConfig(
    key="demo",
    name="Demo-Simulator (kein LLM)",
    kind="ollama",
    base_url="http://127.0.0.1:9",  # nie erreicht: Die Demo läuft mit dem Simulator
    privacy="local",
)
# Modellname → Grundgenauigkeit; zwei Modelle, damit Panel und Inter-Modell-α etwas zeigen.
MODELS = {"demo-simulator-a": 0.8, "demo-simulator-b": 0.7}
_TARGET = re.compile(r'<(?:zieltext|target) id="([^"]+)"[^>]*>\n(.*?)\n</', re.S)


class DemoProvider:
    """Antwortet wie ein Modell, ohne eins zu sein: meist der Code aus „Coder A“, sonst ein
    anderer; schwierige Einheiten (fest je Einheit) irren öfter und bei geringerer Konfidenz."""

    def __init__(self, model: str, gold: dict[str, str], codes: list[str]) -> None:
        self.model, self.gold, self.codes = model, gold, codes
        self.accuracy = MODELS[model]

    def chat(self, request: ChatRequest) -> ChatResponse:
        match = _TARGET.search(request.messages[-1]["content"])
        unit, text = (match.group(1), match.group(2)) if match else ("", "")
        truth = self.gold.get(unit)
        difficulty = random.Random(f"schwierig|{unit}").random()  # gleich für alle Modelle
        rng = random.Random(f"{self.model}|{unit}|{request.seed}")
        p_correct = min(0.98, max(0.3, self.accuracy + 0.15 - 0.35 * difficulty))
        others = [c for c in self.codes if c != truth]
        code = truth if truth in self.codes and rng.random() < p_correct else rng.choice(others)
        alternative = rng.choice([c for c in self.codes if c != code])
        confidence = round(100 * min(0.97, 0.55 + 0.4 * (1 - difficulty) + rng.uniform(-0.1, 0.1)))
        words = re.findall(r"\w{4,}", text) or re.findall(r"\w+", text) or [text.strip()]
        answer = {
            "rationale": "Simulierte Antwort des Demo-Simulators (kein LLM).",
            "candidates": [
                {"evidence": words[:1], "code": code, "confidence": confidence},
                {
                    "evidence": words[1:2] or words[:1],
                    "code": alternative,
                    "confidence": 100 - confidence,
                },
            ],
        }
        return ChatResponse(
            text=json.dumps(answer, ensure_ascii=False),
            model=self.model,
            tokens_in=sum(len(m["content"]) for m in request.messages) // 4,
            tokens_out=60,
            latency_s=0.0,
            output_mode="json_schema",
            finish_reason="stop",
        )

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(m, digest=f"demo-{m}", context_length=32768) for m in MODELS]

    def close(self) -> None:
        pass


DEMO_CREATED = "demo_created"


def is_demo(project: Project) -> bool:
    return any(e.event == DEMO_CREATED for e in project.store.audit())


def demo_note() -> str:
    """Hinweis für alle Ausgaben eines Demo-Projekts, in der aktuellen Ausgabesprache."""
    return t(
        "Demo-Projekt: Die Antworten stammen von einem Simulator, nicht von einem LLM. "
        "Ergebnisse und Kennwerte dienen nur zur Ansicht und sind nicht zu berichten.",
        "Demo project: the answers come from a simulator, not from an LLM. Results and "
        "metrics are for illustration only and must not be reported.",
    )


def create_demo(folder: Path) -> Project:
    """Legt das Demo-Projekt an und kodiert es mit dem Simulator (dauert wenige Sekunden)."""
    project = Project.create(folder, name="Demo: Unterrichtsgespräche (simulierte Antworten)")
    try:
        project.import_data(
            DEMO_DIR / "daten.xlsx",
            "Turns",
            ColumnMapping(
                id="ID",
                text=["Text"],
                group="Transkript",
                order="Nr",
                speaker="Sprecher",
                gold=["Coder A", "Coder B"],
            ),
        )
        project.load_codebook(DEMO_DIR / "codebuch.md")
        assert project.config.codebook is not None
        codes = [Code(id=s.id, label=s.label) for s in suggest_auto(project.codebook_text or "")]
        project.update(
            codebook=project.config.codebook.model_copy(update={"codes": codes}),
            coding=project.config.coding.model_copy(
                update={"units": UnitSettings(mode="context", n_before=2, n_after=1)}
            ),
            providers=[*project.config.providers, DEMO_PROVIDER],
            panel=[ModelConfig(provider=DEMO_PROVIDER.key, model=m) for m in MODELS],
        )
        project.save()
        allowed = project.config.codebook.allowed_ids()
        gold = {u.unit_id: str(u.gold.get("Coder A")) for u in project.store.units()}
        Engine(
            project, provider_factory=lambda _: _Panel(gold, allowed), sleep=lambda s: None
        ).run()
        project.store.log(DEMO_CREATED, models=list(MODELS))
    except BaseException:
        project.close()
        raise
    return project


class _Panel:
    """Ein Anbieter für beide Simulator-Modelle: wählt je Anfrage das passende."""

    def __init__(self, gold: dict[str, str], codes: list[str]) -> None:
        self._models = {m: DemoProvider(m, gold, codes) for m in MODELS}

    def chat(self, request: ChatRequest) -> ChatResponse:
        return self._models[request.model].chat(request)

    def list_models(self) -> list[ModelInfo]:
        return next(iter(self._models.values())).list_models()

    def close(self) -> None:
        pass
