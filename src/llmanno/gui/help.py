"""Anleitung im Programm: Hilfefenster mit Inhalt, Suche und Querverweisen; F1 öffnet den
Abschnitt zum aktuellen Tab. Quelle ist docs/ANLEITUNG.md, bei englischer Oberfläche
docs/MANUAL.md mit denselben Überschriften (beide in der Exe mitgeliefert)."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import llmanno
from llmanno.gui import common

GLOSSARY = "Glossar"
SCHEME = "hilfe"  # Verweis auf einen Abschnitt: hilfe:<Nummer>
GERMAN, ENGLISH = "ANLEITUNG.md", "MANUAL.md"


@dataclass(frozen=True)
class Section:
    level: int  # 1 bis 3 wie die Überschrift
    title: str
    text: str  # Markdown samt Überschrift und Unterabschnitten


def manual_path(name: str = GERMAN) -> Path | None:
    """In der Exe im Paketordner (PyInstaller), aus dem Quelltext unter docs/."""
    candidates = []
    if bundle := getattr(sys, "_MEIPASS", None):
        candidates.append(Path(bundle) / "llmanno" / "help" / name)
    candidates.append(Path(llmanno.__file__).resolve().parents[2] / "docs" / name)
    return next((p for p in candidates if p.is_file()), None)


def sections(text: str) -> list[Section]:
    """Abschnitte nach den Überschriften #, ## und ###; Codeblöcke zählen nicht."""
    lines = text.splitlines()
    heads: list[tuple[int, int, str]] = []
    fence = False
    for i, line in enumerate(lines):
        if line.startswith("```"):
            fence = not fence
        elif not fence and (match := re.match(r"(#{1,3}) (.+)", line)):
            heads.append((i, len(match[1]), match[2].strip()))
    out = []
    for n, (start, level, title) in enumerate(heads):
        end = next((i for i, lv, _ in heads[n + 1 :] if lv <= level), len(lines))
        out.append(Section(level, title, "\n".join(lines[start:end]).strip()))
    return out


class HelpWindow(QDialog):
    """Nicht modal: bleibt neben dem Hauptfenster offen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Anleitung"))
        self.resize(1000, 720)
        path = manual_path()
        self.sections = sections(path.read_text(encoding="utf-8")) if path else []
        self._keys = [s.title for s in self.sections]  # die Tabs nennen deutsche Titel
        self.german = True
        if common.UI_LANGUAGE != "de" and (path := manual_path(ENGLISH)):
            english = sections(path.read_text(encoding="utf-8"))
            if [s.level for s in english] == [s.level for s in self.sections]:  # gleich gebaut
                self.sections, self.german = english, False
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr("In der Anleitung suchen …"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._chosen)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._link)
        self._items: list[QTreeWidgetItem] = []
        self._related: list[int] = []
        parents: dict[int, QTreeWidgetItem] = {}
        for index, section in enumerate(self.sections):
            if section.level == 1:  # der Titel „Anleitung“
                self._items.append(QTreeWidgetItem())
                continue
            item = QTreeWidgetItem([section.title])
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            if section.level == 3 and 2 in parents:
                parents[2].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            parents[section.level] = item
            self._items.append(item)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.tree)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.view)
        split.setSizes([280, 720])
        layout = QVBoxLayout(self)
        if common.UI_LANGUAGE != "de" and self.german:
            layout.addWidget(QLabel(self.tr("Die Anleitung gibt es bisher nur auf Deutsch.")))
        layout.addWidget(split)
        if not self.sections:
            self.view.setPlainText(
                self.tr("Die Anleitung wurde nicht gefunden (docs/ANLEITUNG.md).")
            )

    def index(self, title: str) -> int | None:
        """Abschnitt zum deutschen Titel, auch in der englischen Anleitung (gleiche Stelle)."""
        return next((i for i, t in enumerate(self._keys) if t == title), None)

    def show_topic(self, title: str, related: tuple[str, ...] = ()) -> None:
        """Abschnitt zeigen; `related` erscheint darunter als „Siehe auch“."""
        index = self.index(title)
        self._related = [i for t in related if (i := self.index(t)) is not None and i != index]
        if index is not None:
            self.search.clear()
            self.tree.setCurrentItem(self._items[index])
            self._render(index)  # auch wenn der Eintrag schon gewählt war
        self.show()
        self.raise_()
        self.activateWindow()

    def _chosen(self, item: QTreeWidgetItem | None) -> None:
        if item is not None:
            self._render(item.data(0, Qt.ItemDataRole.UserRole))

    def _render(self, index: int) -> None:
        text = self.sections[index].text
        links = [i for i in self._related if i != index]
        if links:
            text += (
                "\n\n---\n\n**"
                + self.tr("Siehe auch:")
                + "** "
                + " · ".join(f"[{self.sections[i].title}]({SCHEME}:{i})" for i in links)
            )
        self.view.setMarkdown(text)
        self.view.verticalScrollBar().setValue(0)

    def _link(self, url: QUrl) -> None:
        if url.scheme() == SCHEME:
            index = int(url.path())
            self.tree.setCurrentItem(self._items[index])
            self._render(index)
        else:
            QDesktopServices.openUrl(url)

    def _filter(self, query: str) -> None:
        """Nur Abschnitte, die den Suchtext enthalten (ohne Groß-/Kleinschreibung)."""
        needle = query.strip().lower()
        first = None
        for index, item in enumerate(self._items):
            if item.treeWidget() is None:
                continue
            # Ein Abschnitt enthält seine Unterabschnitte: Eltern von Treffern bleiben sichtbar.
            hit = not needle or needle in self.sections[index].text.lower()
            item.setHidden(not hit)
            item.setExpanded(bool(needle) and hit)
            if hit and needle and first is None:
                first = item
        if first is not None:
            self.tree.setCurrentItem(first)


def help_window(owner: QWidget) -> HelpWindow:
    """Ein Hilfefenster je Hauptfenster."""
    window = getattr(owner, "_help_window", None)
    if window is None:
        window = HelpWindow(owner)
        owner._help_window = window  # type: ignore[attr-defined]
    return window


def show_help(owner: QWidget, topics: tuple[str, ...]) -> None:
    """Erster Abschnitt aus `topics`, die übrigen und das Glossar als „Siehe auch“."""
    title, *related = topics or (GLOSSARY,)
    help_window(owner).show_topic(title, (*related, GLOSSARY))
