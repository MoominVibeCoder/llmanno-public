"""Roadmap 40: saubere Fehlermeldungen und Versionsschutz."""

import json
import zipfile

import pytest
from helpers import FakeProvider, ans, engine

from llmanno.core.project import CONFIG_FILE, Project
from llmanno.rerun import main


@pytest.fixture
def folder(tmp_path):
    project = Project.create(tmp_path / "p", name="P")
    project.close()
    return tmp_path / "p"


def _edit(folder, change):
    path = folder / CONFIG_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_newer_project_says_so(folder):
    _edit(folder, lambda d: d.update(config_version=2))
    with pytest.raises(ValueError, match="neueren llmanno-Version") as info:
        Project.open(folder)
    assert "validation error" not in str(info.value)


def test_unknown_settings_point_to_a_newer_version(folder):
    _edit(folder, lambda d: d["coding"].update(neues_feld=1))
    with pytest.raises(ValueError, match=r"nicht kennt \(coding\.neues_feld\)") as info:
        Project.open(folder)
    assert "pydantic" not in str(info.value) and "aktualisieren" in str(info.value)


def test_invalid_or_broken_project_json(folder):
    _edit(folder, lambda d: d["coding"].update(n_runs="viele"))
    with pytest.raises(ValueError, match=r"project\.json ist ungültig \(coding\.n_runs: "):
        Project.open(folder)
    (folder / CONFIG_FILE).write_text("{kaputt", encoding="utf-8")
    with pytest.raises(ValueError, match="beschädigt"):
        Project.open(folder)


def test_rerun_reports_broken_packages_without_traceback(tmp_path, capsys):
    broken = tmp_path / "kaputt.zip"
    broken.write_bytes(b"kein zip")
    assert main([str(broken), "--out", str(tmp_path / "o1")]) == 1
    assert "kein ZIP-Archiv" in capsys.readouterr().err

    partial = tmp_path / "teil.zip"
    with zipfile.ZipFile(partial, "w") as archive:
        archive.writestr("replikation/README.md", "x")
    assert main([str(partial), "--out", str(tmp_path / "o2")]) == 1
    assert "project.json fehlt" in capsys.readouterr().err

    assert main([str(tmp_path / "fehlt.zip"), "--out", str(tmp_path / "o3")]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_failed_format_repair_is_not_counted_as_repaired(ready_project):
    project = ready_project
    summary = engine(project, FakeProvider(lambda r, n: "kein json")).run(item_limit=1)
    call = project.store.calls()[0]
    assert call.status == "invalid" and "repair_gescheitert" in call.flags
    assert "repariert" not in call.flags and summary.repaired == 0


def test_evidence_repair_counts_only_when_adopted(ready_project):
    project = ready_project
    # Der Repair zitiert wieder erfunden: nicht besser belegt, also verworfen.
    summary = engine(project, FakeProvider(lambda r, n: ans(evidence=["erfunden"]))).run(
        item_limit=1
    )
    call = project.store.calls()[0]
    assert "repair_verworfen" in call.flags and "repariert" not in call.flags
    assert summary.repaired == 0
