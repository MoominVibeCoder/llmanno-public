"""Ampelkasten oben im Evaluation-Tab: das Kurzurteil aus core.verdict in Klartext."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QObject

from llmanno.core.verdict import Check, Verdict
from llmanno.gui import common

COLORS = {"green": "#2e9e44", "yellow": "#e0a800", "red": "#d93a3a", "grey": "#8a8a8a"}


def _n(value: float | str | None, digits: int = 2) -> str:
    """Wie die Schwellen im Text: Dezimalkomma auf Deutsch; nicht berechenbar als „–“."""
    if not isinstance(value, int | float) or value != value:  # NaN
        return "–"
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if common.UI_LANGUAGE == "de" else text


class VerdictText(QObject):
    """Übersetzbare Texte des Kurzurteils (Kontext für Qt-Übersetzungen)."""

    def html(self, v: Verdict) -> str:
        rows = "".join(
            f'<tr><td style="color:{COLORS[c.level]}; font-size:large; padding-right:6px">●</td>'
            f"<td>{escape(self._line(c, v))}</td></tr>"
            for c in v.checks
        )
        title = escape(self.tr("Kurzurteil"))
        now = escape(self.tr("Was jetzt:"))
        caveat = escape(self.tr("Faustregeln, keine Gesetze; die Kennwerte stehen darunter."))
        return (
            f"<h3>{title}</h3><p>{escape(self._scope(v))}</p><table>{rows}</table>"
            f"<p><b>{now}</b> {escape(self._next(v))}</p><p><i>{caveat}</i></p>"
        )

    def _scope(self, v: Verdict) -> str:
        if v.subset == "test":
            return self.tr("Prüfteil (Test): die unabhängige Validierung des Instruments.")
        if v.subset == "dev":
            return self.tr("Übungsteil (Dev): zum Schärfen, noch keine unabhängige Validierung.")
        if v.has_split:
            return self.tr("Alle Einheiten, einschließlich des Übungsteils (Dev).")
        return self.tr(
            "Alle Einheiten, ohne Aufteilung: Wer Codebuch oder Anweisungen nach diesen Werten "
            "ändert, kann sie nicht zugleich als unabhängige Validierung berichten."
        )

    def _line(self, c: Check, v: Verdict) -> str:
        x = c.values
        if c.key == "alpha":
            if c.level == "grey":
                return self.tr(
                    "Übereinstimmung mit {0}: Krippendorffs α ist nicht berechenbar (etwa weil "
                    "nur ein Code vorkommt)."
                ).format(v.reference)
            head = self.tr("Übereinstimmung mit {0}: Krippendorffs α = {1} [{2}; {3}]").format(
                v.reference, _n(x["alpha"]), _n(x["low"]), _n(x["high"])
            )
            tail = {
                "green": self.tr("belastbar (ab 0,80)"),
                "yellow": self.tr("vorläufig (0,667 bis 0,80)"),
                "red": self.tr("zu niedrig (unter 0,667)"),
            }[c.level]
            if x.get("cause") == "uncertain":
                tail = self.tr("hoch, aber unsicher: die untere Intervallgrenze liegt unter 0,667")
            return f"{head}: {tail}"
        if c.key == "humans":
            if c.level == "grey":
                return self.tr(
                    "Kein Vergleich mit Menschen: Dafür braucht es mindestens zwei Gold-Spalten "
                    "auf denselben Einheiten."
                )
            text = self.tr("Menschen untereinander α = {0}, das LLM {1}: {2}").format(
                _n(x["humans"]), _n(x["llm"]),
                {
                    "green": self.tr("auf dem Niveau der Menschen"),
                    "yellow": self.tr("etwas schwächer als die Menschen"),
                    "red": self.tr("deutlich schwächer als die Menschen"),
                }[c.level],
            )  # fmt: skip
            if x.get("alt_test") == "passed":
                text += " · " + self.tr("Alt-Test bestanden")
            elif x.get("alt_test") == "failed":
                text += " · " + self.tr("Alt-Test nicht bestanden")
            return text
        if c.key == "weakest":
            if c.level == "grey":
                if not x.get("codes"):
                    return self.tr("Je Code liegen keine Angaben vor.")
                return self.tr(
                    "Kein Code hat {0} Referenzfälle; je Code ist noch keine Aussage möglich."
                ).format(x["min_support"])
            text = self.tr("Schwächster Code: {0} (F1 {1}): {2}").format(
                x["code"], _n(x["f1"]),
                {
                    "green": self.tr("alle bewerteten Codes ab 0,70"),
                    "yellow": self.tr("unter 0,70, Definition und Abgrenzung prüfen"),
                    "red": self.tr("unter 0,50, so nicht verwendbar"),
                }[c.level],
            )  # fmt: skip
            if x.get("unrated"):
                text += " · " + self.tr("{0} Codes mit zu wenigen Fällen nicht bewertet").format(
                    x["unrated"]
                )
            return text
        if c.key == "basis":
            if v.by_review:
                if c.level == "green":
                    return self.tr(
                        "Genug Reviews: {0} Einheiten, das Intervall von α ist {1} breit."
                    ).format(x["n"], _n(x["width"]))
                return self.tr(
                    "Wenige Reviews: {0} Einheiten, das Intervall von α ist {1} breit. Für eine "
                    "genaue Aussage mehr Einheiten der Zufallsstichprobe blind reviewen "
                    "(Tab Review)."
                ).format(x["n"], _n(x["width"]))
            if c.level == "green":
                return self.tr(
                    "Genug Gold: {0} Einheiten, das Intervall von α ist {1} breit."
                ).format(x["n"], _n(x["width"]))
            return self.tr(
                "Wenig Gold: {0} Einheiten, das Intervall von α ist {1} breit. Für eine genaue "
                "Aussage mehr Gold sammeln (Tab Splits & Freeze: Wie viel Gold?)."
            ).format(x["n"], _n(x["width"]))
        # robust
        if c.level == "grey":
            return self.tr(
                "Robustheit nicht geprüft: Prompt-Sensitivität (Tab Lauf) oder Subgruppen "
                "(Analyse-Variablen im Tab Daten) fehlen."
            )
        if c.level == "green":
            if x.get("wording") and x.get("subgroups"):
                return self.tr(
                    "Robust: Die Codes hängen nicht am Wortlaut, und es gibt keine Messfehler "
                    "nach Subgruppen."
                )
            if x.get("wording"):
                return self.tr(
                    "Die Codes hängen nicht am Wortlaut; Subgruppen nicht geprüft "
                    "(Analyse-Variablen im Tab Daten)."
                )
            return self.tr(
                "Keine Messfehler nach Subgruppen; den Wortlaut nicht geprüft "
                "(Prompt-Sensitivität im Tab Lauf)."
            )
        parts = []
        if x.get("unstable"):
            parts.append(self.tr("Codes hängen am Wortlaut ({0})").format(x["unstable"]))
        if x.get("biased"):
            parts.append(self.tr("Messfehler nach Subgruppen ({0})").format(x["biased"]))
        return self.tr("Achtung: {0}").format("; ".join(parts))

    def _next(self, v: Verdict) -> str:
        return {
            "sharpen_dev": self.tr(
                "Die häufigsten Verwechslungen ansehen (Konfusionsmatrix; Fälle und "
                "Begründungen unter Tab Codebuch → Unstimmigkeiten prüfen…), Codebuch oder "
                "Anweisungen schärfen, den Übungsteil neu kodieren und auswerten."
            ),
            "sharpen": self.tr(
                "Die häufigsten Verwechslungen ansehen (Konfusionsmatrix; Fälle und "
                "Begründungen unter Tab Codebuch → Unstimmigkeiten prüfen…), Codebuch oder "
                "Anweisungen schärfen und neu kodieren. Ohne Aufteilung gelten die Werte danach "
                "nicht mehr als unabhängige Validierung."
            ),
            "more_gold": self.tr("Mehr Gold sammeln, dann neu auswerten."),
            "more_reviews": self.tr(
                "Mehr Einheiten der Zufallsstichprobe blind reviewen (Tab Review), dann neu "
                "auswerten."
            ),
            "broken": self.tr(
                "Der Freeze ist gebrochen: Diese Auswertung gilt nicht als unabhängige "
                "Validierung. Änderungen zurücknehmen oder den Bruch im Report begründen."
            ),
            "split": self.tr(
                "Im Tab Splits & Freeze aufteilen: Erst die Auswertung des Prüfteils nach dem "
                "Einfrieren ist eine unabhängige Validierung."
            ),
            "freeze": self.tr(
                "Im Tab Splits & Freeze einfrieren, dann alle Einheiten kodieren und den "
                "Prüfteil einmal auswerten."
            ),
            "evaluate_test": self.tr("Den Prüfteil (Test) einmal auswerten."),
            "report": self.tr(
                "Methoden-Report exportieren; Häufigkeiten nach PPI++ berichten, nicht die "
                "naiven Anteile des LLM."
            ),
            "rethink": self.tr(
                "Der Prüfteil zeigt, dass das Instrument so nicht trägt. Den Befund berichten; "
                "ein überarbeitetes Instrument braucht einen neuen Prüfteil."
            ),
        }[v.next_step]
