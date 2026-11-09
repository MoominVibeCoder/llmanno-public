import json

import pytest
from helpers import write_xlsx
from pydantic import ValidationError

from llmanno.core.config import Code, CodebookConfig, ColumnMapping, ModelConfig
from llmanno.core.project import CONFIG_FILE, STORE_FILE, Project


@pytest.fixture
def project(tmp_path):
    project = Project.create(tmp_path / "studie", name="Studie")
    yield project
    project.close()


def test_create_writes_config_store_and_audit(project):
    assert (project.folder / CONFIG_FILE).is_file()
    assert (project.folder / STORE_FILE).is_file()
    [entry] = project.store.audit()
    assert entry.event == "project_created"
    assert entry.details["config"]["name"] == "Studie"
    assert not project.dirty


def test_save_and_reopen_roundtrip(project):
    project.update(mode="multi", k=2, prompt_language="en")
    assert project.dirty
    project.save()
    project.close()

    reopened = Project.open(project.folder)
    assert reopened.config == project.config
    assert not reopened.dirty
    reopened.close()


def test_save_logs_only_changed_fields(project):
    project.update(k=1)
    project.save()
    project.save()  # nichts geändert: kein zweiter Eintrag
    events = [e for e in project.store.audit() if e.event == "config_changed"]
    assert [e.details["changes"] for e in events] == [{"k": [3, 1]}]


@pytest.mark.parametrize("changes", [{"k": 0}, {"k": 4}, {"mode": "both"}, {"name": "  "}])
def test_update_rejects_invalid_values(project, changes):
    with pytest.raises(ValidationError):
        project.update(**changes)
    assert not project.dirty


def test_create_refuses_non_empty_folder(tmp_path):
    (tmp_path / "datei.txt").write_text("x")
    with pytest.raises(FileExistsError):
        Project.create(tmp_path, name="Studie")


def test_open_folder_without_project(tmp_path):
    with pytest.raises(FileNotFoundError, match=CONFIG_FILE):
        Project.open(tmp_path)


def test_open_rejects_newer_config_version(project):
    path = project.folder / CONFIG_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "config_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="Projektversion 99"):
        Project.open(project.folder)


def test_import_data_copies_file_and_logs(project, tmp_path):
    source = write_xlsx(tmp_path / "Quelle.XLSX", ["ID", "Text"], [[1, "a"], [2, "b"], [2, "c"]])
    report = project.import_data(source, "Daten", ColumnMapping(id="ID", text=["Text"]))
    assert len(report.units) == 2 and report.n_skipped == 1
    assert project.data_path == project.folder / "data.xlsx" and project.data_path.is_file()
    assert project.config.data.n_units == 2
    assert not project.dirty
    event = project.store.audit()[-1]
    assert (event.event, event.details["n_units"], event.details["n_skipped"]) == (
        "data_imported",
        2,
        1,
    )


def test_import_without_valid_units_changes_nothing(project, tmp_path):
    source = write_xlsx(tmp_path / "leer.xlsx", ["ID", "Text"], [[1, ""]])
    with pytest.raises(ValueError, match="Keine gültige Einheit"):
        project.import_data(source, "Daten", ColumnMapping(id="ID", text=["Text"]))
    assert project.config.data is None and project.store.units() == []


def test_load_codebook_keeps_confirmed_codes(project, tmp_path):
    path = tmp_path / "cb.txt"
    path.write_text("### K1 — Eins\n", encoding="utf-8")
    project.load_codebook(path)
    assert project.codebook_text == "### K1 — Eins\n"
    project.update(codebook=project.config.codebook.model_copy(update={"codes": [Code(id="K1")]}))
    project.save()

    path.write_text("### K1 — Eins, geschärft\n", encoding="utf-8")
    project.load_codebook(path)
    assert [c.id for c in project.config.codebook.codes] == ["K1"]
    assert project.store.audit()[-1].event == "codebook_loaded"


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"codes": [{"id": "K1"}, {"id": "K1"}]}, "Doppelte"),
        ({"codes": [{"id": "KEIN_CODE"}]}, "kollidiert"),
        ({"codes": [{"id": "K 1"}]}, "pattern"),
        ({"scale": "ordinal", "codes": [{"id": "K1"}]}, "Rang"),
    ],
)
def test_codebook_rules(fields, message):
    with pytest.raises(ValidationError, match=message):
        CodebookConfig(source_name="c", sha256="x", encoding="utf-8", **fields)


def test_panel_must_reference_known_unique_providers(project):
    with pytest.raises(ValidationError, match="Unbekannter Anbieter"):
        project.update(panel=[ModelConfig(provider="fehlt", model="m")])
    with pytest.raises(ValidationError, match="nur einmal"):
        project.update(panel=[ModelConfig(provider="ollama", model="m")] * 2)


def test_effective_temperature():
    model = ModelConfig(provider="ollama", model="m")
    assert (model.effective_temperature(5), model.effective_temperature(1)) == (0.7, 0.0)
    assert (
        ModelConfig(provider="ollama", model="m", temperature=0.2).effective_temperature(5) == 0.2
    )


def test_replace_file_retries_on_permission_error(tmp_path, monkeypatch):
    from llmanno.core import project as project_module

    source, target = tmp_path / "a.tmp", tmp_path / "a.json"
    source.write_text("neu")
    calls = {"n": 0}
    original = type(source).replace

    def flaky(self, other):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("gesperrt")
        return original(self, other)

    monkeypatch.setattr(type(source), "replace", flaky)
    monkeypatch.setattr(project_module.time, "sleep", lambda s: None)
    project_module.replace_file(source, target)
    assert target.read_text() == "neu" and calls["n"] == 3
