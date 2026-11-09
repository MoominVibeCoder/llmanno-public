"""Jeder Oberflächentext steht in der Übersetzung (Roadmap 34).

pyside6-lupdate erfasst tr() in f-Strings nicht; solche Texte blieben in der englischen
Oberfläche deutsch. Der Test liest alle tr()- und translate()-Aufrufe mit festem Text per AST
und prüft, dass jeder in llmanno_en.ts übersetzt ist.
"""

import ast
from pathlib import Path
from xml.etree import ElementTree as ET

GUI = Path(__file__).resolve().parents[1] / "src" / "llmanno" / "gui"
TS = GUI.parent / "i18n" / "llmanno_en.ts"


def _sources() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(GUI.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            name, args = node.func.attr, node.args
            text = None
            if name == "tr" and args and isinstance(args[0], ast.Constant):
                text = args[0].value
            elif name in ("translate", "QT_TRANSLATE_NOOP") and len(args) > 1:
                if isinstance(args[1], ast.Constant):
                    text = args[1].value
            if isinstance(text, str):
                found.setdefault(text, []).append(f"{path.name}:{node.lineno}")
    return found


def test_every_ui_text_is_translated():
    translated = {
        m.findtext("source"): m.find("translation") for m in ET.parse(TS).getroot().iter("message")
    }
    missing = {
        text: where
        for text, where in _sources().items()
        if text not in translated
        or translated[text].get("type") in ("unfinished", "vanished", "obsolete")
        or not (translated[text].text or "").strip()
    }
    assert not missing, "\n".join(f"{w[0]}: {t!r}" for t, w in sorted(missing.items()))
