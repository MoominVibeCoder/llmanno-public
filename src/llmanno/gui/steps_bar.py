"""Leiste „Nächster Schritt“ unter den Tabs: erledigte Schritte mit Häkchen, der nächste mit
kurzer Anleitung und einem Knopf zum passenden Tab (core.steps)."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from llmanno.core.steps import Step, next_step

LINK = "schritt:"


class StepsBar(QWidget):
    go_to = Signal(str)  # Schlüssel des Schritts, den die Leiste anbietet

    def __init__(self) -> None:
        super().__init__()
        self.chain = QLabel()
        self.chain.setTextFormat(Qt.TextFormat.RichText)
        self.chain.linkActivated.connect(lambda link: self.go_to.emit(link.removeprefix(LINK)))
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.button = QPushButton(self.tr("Zum Tab"))
        self.button.clicked.connect(self._go)
        self._next: Step | None = None
        row = QHBoxLayout()
        row.addWidget(self.hint, 1)
        row.addWidget(self.button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(self.chain)
        layout.addLayout(row)

    def _go(self) -> None:
        if self._next is not None:
            self.go_to.emit(self._next.key)

    def names(self) -> dict[str, str]:
        return {
            "data": self.tr("Daten"),
            "codebook": self.tr("Codebuch"),
            "models": self.tr("Modelle"),
            "split": self.tr("Aufteilen"),
            "pilot": self.tr("Pilot"),
            "dev": self.tr("Übungsteil"),
            "freeze": self.tr("Einfrieren"),
            "full": self.tr("Alle Einheiten"),
            "test": self.tr("Prüfteil auswerten"),
            "review": self.tr("Prüfliste"),
            "export": self.tr("Export"),
        }

    def hints(self) -> dict[str, str]:
        return {
            "data": self.tr("Im Tab Daten die Tabelle mit den Texten importieren."),
            "codebook": self.tr("Im Tab Codebuch das Codebuch laden und die Code-Liste prüfen."),
            "models": self.tr(
                "Im Tab Modelle einen Anbieter einrichten und ein Modell ins Panel übernehmen."
            ),
            "split": self.tr(
                "Im Tab Splits & Freeze Übungsteil (Dev) und Prüfteil (Test) bilden, bevor "
                "kodiert wird."
            ),
            "pilot": self.tr(
                "Im Tab Lauf den Pilot starten: 20 Einheiten, um Modell und Anweisungen zu testen."
            ),
            "dev": self.tr(
                "Im Tab Lauf mit „nur Übungsteil (Dev) kodieren“ den Übungsteil kodieren, in der "
                "Evaluation prüfen und Codebuch oder Anweisungen schärfen, bis es passt."
            ),
            "freeze": self.tr(
                "Passt die Evaluation auf dem Übungsteil, im Tab Splits & Freeze das Instrument "
                "einfrieren."
            ),
            "full": self.tr("Im Tab Lauf den Vollauf starten: Er kodiert alle Einheiten."),
            "test": self.tr(
                "In der Evaluation den Prüfteil (Test) einmal auswerten: Das ist die unabhängige "
                "Validierung."
            ),
            "review": self.tr(
                "Im Tab Review eine Prüfliste erzeugen und die Zufallsstichprobe blind kodieren: "
                "Ohne Gold misst nur sie, wie gut das LLM kodiert."
            ),
            "export": self.tr("Im Tab Export Methoden-Report und Replikationspaket speichern."),
        }

    def show_steps(self, steps: list[Step]) -> None:
        names = self.names()
        self._next = next_step(steps)
        parts = []
        for step in steps:
            name = escape(names[step.key])
            link = f'<a href="{LINK}{step.key}">{name}</a>'
            if step.done:
                parts.append(f"✓ {link}")
            elif step is self._next:
                parts.append(f"<b>● {link}</b>")
            else:
                parts.append(f"○ {link}")
        self.chain.setText(" › ".join(parts))
        if self._next is None:
            self.hint.setText(self.tr("Alle Schritte erledigt."))
        elif self._next.broken:
            self.hint.setText(
                self.tr(
                    "Nächster Schritt: Der Freeze ist gebrochen. Im Tab Splits & Freeze die "
                    "Änderungen zurücknehmen oder den Freeze mit Begründung aufheben und neu "
                    "einfrieren."
                )
            )
        else:
            self.hint.setText(self.tr("Nächster Schritt: {0}").format(self.hints()[self._next.key]))
        self.button.setVisible(self._next is not None)
