"""Feste Werte für Fingerabdruck, Lauf-Identität und gerenderten Prompt (Roadmap 7).

Ändert sich hier ein Wert, ändern sich die Identität bestehender Läufe und der Freeze: Alte
Ergebnisse zählen dann nicht mehr, ein gefrorenes Instrument gilt als gebrochen. Bevor du
einen Wert neu setzt:
- Prompt-Text geändert: TEMPLATE_VERSION in core/prompt.py erhöhen, dann die Werte anpassen.
- Neues Konfigurationsfeld mit Default: so aufnehmen, dass der Default nicht in den Hash geht
  (wie _LATER_FIELDS in core/engine.py), sonst brechen alle Freezes.
"""

from dataclasses import replace

import pytest
from helpers import FakeProvider, ans, write_xlsx

from llmanno.core.config import KI_CONNECT_NRW, Code, ColumnMapping, ModelConfig, UnitSettings
from llmanno.core.data_import import units_sha256
from llmanno.core.engine import Engine, fingerprint, prepare_prompts
from llmanno.core.project import Project
from llmanno.core.prompt import TEMPLATE_VERSION, render
from llmanno.core.store import sha256_text

ROWS = [
    ["T1", 1, "I", "Warum ist das so?", "Klasse 7", "K1"],
    ["T1", 2, "S", "Weil die Masse größer ist.", "Klasse 7", "K2"],
    ["T1", 3, "I", "=Gut, und was folgt daraus?", "Klasse 7", "K1"],
    ["T2", 1, "S", "Ich weiß es nicht.", "Klasse 8", None],
    ["T2", 2, "I", "Überleg noch einmal.", "Klasse 8", "K3"],
]
CODEBOOK = (
    "### K1 — Frage\nEine Frage der Lehrkraft.\n\n"
    "### K2 — Erklärung\nEine Begründung mit „weil“.\n\n"
    "### K3 — Rückmeldung\nLob oder Korrektur.\n"
)

GOLDEN = {
    "template_version": "2",
    "units_sha256_legacy": "0322f281090e50d0cef7e62949c1139006cfd1da3463c7c52d1f6a1a2be5a9da",
    "fingerprint_groups": "1ca1b98f94e7b042cdd83105d08c9e21a4f5f3b647a1fcf799b9f0c81a31620f",
    "units_sha256": "361d275c011de5e76eee469c521ac3b88b0ccbe312311ddd6b979a2edcb91f56",
    "fingerprint": "657f8c82879537ae65d423019654a98e75989b0c544ee176264db9e9281e8d47",
    "config_hash_ollama": "9d18bdec392c1202",
    "config_hash_openai": "0bdfa9fe66ecee75",
    "system_de_context_single": "f91fac330f24a7e867d3434e2c39fec06e49fa903a6af847e07283e0d70d0421",
    "user_de_context_single": "083d9e89cb7e28abbbb1318f0f5a111d86512b7ebf2c836a357ecbb8284691ec",
    "system_en_isolated_multi": "475184f92db683d88e32b04b0274db26ca0f34c4fb4b2da43368cb6b10cbf084",
    "user_en_isolated_multi": "f5051e55db6f347dc41fc4fd0b536bb071f030496af2b5950061de10be125ff5",
    "system_de_variant_u1": "e232df500ac6b5ff351398eecc993302e7294c2e0e8e0bdcce534522e6c568f0",
}


@pytest.fixture
def project(tmp_path):
    project = Project.create(tmp_path / "p", name="Golden")
    header = ["Transkript", "Nr", "Sprecher", "Text", "Klasse", "Coder"]
    data = write_xlsx(tmp_path / "d.xlsx", header, ROWS)
    project.import_data(
        data, "Daten",
        ColumnMapping(text=["Text"], group="Transkript", order="Nr", speaker="Sprecher",
                      meta=["Klasse"], gold=["Coder"]),
    )  # fmt: skip
    codebook = tmp_path / "cb.txt"
    codebook.write_text(CODEBOOK, encoding="utf-8")
    project.load_codebook(codebook)
    codes = [Code(id="K1", label="Frage"), Code(id="K2", label="Erklärung"), Code(id="K3")]
    project.update(
        codebook=project.config.codebook.model_copy(update={"codes": codes}),
        # Stopp-Regel fest wie vor dem neuen Default 2: Gespeicherte Projekte behalten ihren Wert.
        coding=project.config.coding.model_copy(
            update={"units": UnitSettings(mode="context"), "adaptive_min_agree": 3}
        ),
        providers=[*project.config.providers, KI_CONNECT_NRW],
        panel=[
            ModelConfig(provider="ollama", model="fake", temperature=0.7),
            ModelConfig(
                provider=KI_CONNECT_NRW.key,
                model="OpenAI GPT OSS 120B",
                provider_temperature=True,
                reasoning_effort="low",
            ),
        ],  # fmt: skip
    )
    yield project
    project.close()


def test_template_version(project):
    assert TEMPLATE_VERSION == GOLDEN["template_version"]


def test_units_hash(project):
    """Ohne Gold und ohne die übrige Quellzeile; ältere Projekte behalten ihren Wert."""
    assert project.config.data.units_sha256 == GOLDEN["units_sha256"]
    legacy = units_sha256(project.store.units(), legacy=True)  # Projekte vor dieser Regel
    assert legacy == GOLDEN["units_sha256_legacy"]


def test_code_groups_only_change_the_fingerprint(project):
    codebook = project.config.codebook
    groups = {"K1": "Lehrkraft", "K2": "Lernende", "K3": "Lehrkraft"}
    codes = [c.model_copy(update={"parent": groups[c.id]}) for c in codebook.codes]
    project.update(codebook=codebook.model_copy(update={"codes": codes}))
    assert fingerprint(project) == GOLDEN["fingerprint_groups"]
    prepared = []
    provider = FakeProvider(lambda r, n: ans(), digest="sha256:abc")
    Engine(project, provider_factory=lambda cfg: provider).prepare(prepared)
    assert prepared[0].config_hash == GOLDEN["config_hash_ollama"]


def test_fingerprint(project):
    assert fingerprint(project) == GOLDEN["fingerprint"]


def test_config_hashes(project):
    provider = FakeProvider(lambda r, n: ans(), digest="sha256:abc")
    prepared = []
    Engine(project, provider_factory=lambda cfg: provider).prepare(prepared)
    ollama, openai = prepared
    assert (ollama.num_ctx, openai.num_ctx, openai.info) == (4096, None, None)
    assert ollama.config_hash == GOLDEN["config_hash_ollama"]
    assert openai.config_hash == GOLDEN["config_hash_openai"]


def test_rendered_prompts(project):
    spec, items = prepare_prompts(project)
    item = items[2]
    prompt = render(spec, item, seed=1)
    assert sha256_text(prompt.system) == GOLDEN["system_de_context_single"]
    assert sha256_text(prompt.user) == GOLDEN["user_de_context_single"]

    project.update(mode="multi", prompt_language="en")
    coding = project.config.coding.model_copy(update={"units": UnitSettings(mode="isolated")})
    project.update(coding=coding)
    spec, items = prepare_prompts(project)
    prompt = render(spec, items[2], seed=2)
    assert sha256_text(prompt.system) == GOLDEN["system_en_isolated_multi"]
    assert sha256_text(prompt.user) == GOLDEN["user_en_isolated_multi"]


def test_rendered_variant(project):
    spec, _ = prepare_prompts(project)
    system = render(replace(spec, variant="u1"), prepare_prompts(project)[1][0], seed=1).system
    assert sha256_text(system) == GOLDEN["system_de_variant_u1"]
