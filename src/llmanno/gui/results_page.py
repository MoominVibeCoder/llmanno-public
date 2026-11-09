"""Tab „Ergebnisse“: Tabelle mit Filtern, Detailansicht mit hervorgehobenen Signalwörtern."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QT_TRANSLATE_NOOP, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from llmanno.core.aggregate import PANEL_INCOMPLETE, ItemResult
from llmanno.core.engine import basis_warnings, prepare_prompts, result_basis, results
from llmanno.core.evaluation import reference_labels
from llmanno.core.gold import Label
from llmanno.core.i18n import localized
from llmanno.core.review import unsure
from llmanno.core.store import CallRecord
from llmanno.core.units import Item, Line
from llmanno.core.validation import hidden_test_items
from llmanno.gui import common
from llmanno.gui.common import Page, error_text, fill_combo, heading, hint_label, status_text

# Farben je Rang des finalen Codes; dezent, damit der Text lesbar bleibt.
HIGHLIGHT = ("#fde68a", "#bfdbfe", "#bbf7d0")
DARK_TEXT = "#111827"  # helle Hinterlegung: dunkle Schrift, auch im Dunkelmodus
STATUS_NOTE = {
    "exact": QT_TRANSLATE_NOOP("ResultsPage", "exakt"),
    "normalized": QT_TRANSLATE_NOOP("ResultsPage", "normalisiert"),
    "fuzzy": QT_TRANSLATE_NOOP("ResultsPage", "unscharf"),
    "missing": QT_TRANSLATE_NOOP("ResultsPage", "nicht im Text"),
}
COLUMNS = (
    "ID",
    QT_TRANSLATE_NOOP("ResultsPage", "Text"),
    QT_TRANSLATE_NOOP("ResultsPage", "LLM-Code"),
    QT_TRANSLATE_NOOP("ResultsPage", "Referenz"),
    QT_TRANSLATE_NOOP("ResultsPage", "Konsistenz\n(p_sc)"),
    QT_TRANSLATE_NOOP("ResultsPage", "Selbsteinschätzung\n(p_verbal)"),
    QT_TRANSLATE_NOOP("ResultsPage", "Alternativen"),
    QT_TRANSLATE_NOOP("ResultsPage", "Hinweise"),
    QT_TRANSLATE_NOOP("ResultsPage", "Status"),
)
COLUMN_TIPS = (
    "",
    "",
    QT_TRANSLATE_NOOP(
        "ResultsPage",
        "Der Code, auf den sich die Durchläufe aller Modelle einigen; bei mehreren Codes je "
        "Einheit alle, die in mindestens der Hälfte der Durchläufe vorkommen.",
    ),
    "",  # Referenz: nennt die Gold-Spalte, gesetzt in refresh
    QT_TRANSLATE_NOOP(
        "ResultsPage",
        "Anteil der Durchläufe aller Modelle, die den (ersten) LLM-Code liefern; 1.00 heißt: "
        "alle einig. Das wichtigste Maß für die Unsicherheit.",
    ),
    QT_TRANSLATE_NOOP(
        "ResultsPage",
        "Die Konfidenz, die das Modell selbst nennt (0 bis 1); meist zu optimistisch.",
    ),
    QT_TRANSLATE_NOOP(
        "ResultsPage", "Weitere Codes, die Durchläufe genannt haben, nach Konsistenz geordnet."
    ),
    QT_TRANSLATE_NOOP(
        "ResultsPage",
        "Warnzeichen der Einheit, etwa Dissens im Panel oder nicht belegte Codes; der Filter "
        "oben wählt sie aus.",
    ),
    QT_TRANSLATE_NOOP(
        "ResultsPage",
        "gültig: mindestens ein brauchbares Ergebnis; ungültig: nur unbrauchbare Antworten; "
        "Fehler: keine Antwort.",
    ),
)
# Spaltenbreiten; der Text nimmt den übrigen Platz, damit „Status“ nicht abgeschnitten wird.
WIDTHS = (65, 0, 85, 110, 80, 115, 100, 130, 65)
HINTS_COLUMN = 7

# Hinweise (Flags) im Klartext: Einheiten (core.aggregate) und einzelne Durchläufe
# (core.engine, core.schema). Unbekannte erscheinen unverändert.
FLAG_LABELS = {
    "unentschieden": QT_TRANSLATE_NOOP("ResultsPage", "unentschieden"),
    "kein_code_ueber_schwelle": QT_TRANSLATE_NOOP("ResultsPage", "kein Code über der Schwelle"),
    "dissens": QT_TRANSLATE_NOOP("ResultsPage", "Dissens im Panel"),
    "abstain": QT_TRANSLATE_NOOP("ResultsPage", "kein Code (Restkategorie)"),
    "nicht_belegt": QT_TRANSLATE_NOOP("ResultsPage", "nicht belegt"),
    "ungueltige_laeufe": QT_TRANSLATE_NOOP("ResultsPage", "mit ungültigen Durchläufen"),
    PANEL_INCOMPLETE: QT_TRANSLATE_NOOP("ResultsPage", "Panel unvollständig"),
    "repariert": QT_TRANSLATE_NOOP("ResultsPage", "nach Nachfrage gültig"),
    "repair_gescheitert": QT_TRANSLATE_NOOP("ResultsPage", "Nachfrage gescheitert"),
    "repair_verworfen": QT_TRANSLATE_NOOP("ResultsPage", "Nachfrage verworfen"),
    "ausgabe_abgeschnitten": QT_TRANSLATE_NOOP("ResultsPage", "Antwort abgeschnitten"),
    "doppelter_code": QT_TRANSLATE_NOOP("ResultsPage", "Code doppelt genannt"),
    "zu_viele_signalwoerter": QT_TRANSLATE_NOOP("ResultsPage", "zu viele Signalwörter, gekürzt"),
    "zu_viele_codes": QT_TRANSLATE_NOOP("ResultsPage", "zu viele Codes, gekürzt"),
    "konfidenz_begrenzt": QT_TRANSLATE_NOOP("ResultsPage", "Selbsteinschätzung begrenzt"),
    "konfidenz_normalisiert": QT_TRANSLATE_NOOP("ResultsPage", "Selbsteinschätzung normiert"),
}
FLAG_TIPS = {
    "unentschieden": QT_TRANSLATE_NOOP(
        "ResultsPage",
        "Die zwei häufigsten Codes liegen in der Konsistenz fast gleichauf (weniger als 0,1 "
        "auseinander).",
    ),
    "kein_code_ueber_schwelle": QT_TRANSLATE_NOOP(
        "ResultsPage",
        "Mehrere Codes je Einheit: Kein Code kommt in mindestens der Hälfte der Durchläufe vor.",
    ),
    "dissens": QT_TRANSLATE_NOOP(
        "ResultsPage", "Die Modelle im Panel entscheiden sich unterschiedlich."
    ),
    "abstain": QT_TRANSLATE_NOOP(
        "ResultsPage", "Das LLM vergibt die Restkategorie: Kein Code des Codebuchs passt."
    ),
    "nicht_belegt": QT_TRANSLATE_NOOP(
        "ResultsPage",
        "Für einen vergebenen Code steht keines der genannten Signalwörter im Text.",
    ),
    "ungueltige_laeufe": QT_TRANSLATE_NOOP(
        "ResultsPage",
        "Mindestens ein Durchlauf lieferte eine unbrauchbare Antwort, auch nach Nachfrage.",
    ),
    PANEL_INCOMPLETE: QT_TRANSLATE_NOOP(
        "ResultsPage",
        "Nicht jedes Modell im Panel hat ein gültiges Ergebnis; entschieden haben die übrigen.",
    ),
}


SEARCH_DELAY_MS = 250


class ResultsPage(Page):
    help_topics = (
        "8. Ergebnisse",
        "Begründungen",
    )

    def __init__(self) -> None:
        super().__init__()
        self._items: dict[str, Item] = {}
        self._results: dict[str, ItemResult] = {}
        self._calls: dict[str, list[CallRecord]] = {}
        self._reference: dict[str, Label | None] = {}  # Abweichungsansicht
        self._hidden: set[str] = set()  # Test-Einheiten vor dem Freeze: Gold verborgen
        self._parts: dict[str, str] = {}

        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr("Suche in ID und Text"))
        # Erst nach einer kurzen Tipppause filtern: Bei vielen Einheiten kostet jeder Neuaufbau.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self._fill_table)
        self.search.textChanged.connect(self._search_timer.start)
        self.flag_combo = QComboBox()
        entries = [
            (self.tr("Alle Einheiten"), None, ""),
            *((self.tr(FLAG_LABELS[f]), f, self.tr(FLAG_TIPS[f])) for f in FLAG_TIPS),
            (
                self.tr("weicht von der Referenz ab"),
                "view:deviates",
                self.tr("LLM-Code und Referenz (Gold) unterscheiden sich. Der Prüfteil (Test) "
                        "zählt vor dem Einfrieren nie."),
            ),
            (
                self.tr("unsicher (würde zur Prüfung vorgeschlagen)"),
                "view:unsure",
                self.tr("Konsistenz unter der Schwelle τ oder unentschieden: Diese Einheiten "
                        "schlägt die Auswahl unsicherer Fälle (Gate) zur Prüfung vor. Weitere "
                        "Gründe wie Dissens zeigen die Einträge darüber."),
            ),
            (
                self.tr("ungültig / Fehler"),
                "status:bad",
                self.tr("Kein brauchbares Ergebnis: nur unbrauchbare Antworten (ungültig) oder "
                        "keine Antwort (Fehler)."),
            ),
            (self.tr("noch nicht kodiert"), "status:pending", ""),
        ]  # fmt: skip
        fill_combo(self.flag_combo, [(text, data) for text, data, _ in entries])
        for index, (_, _, tip) in enumerate(entries):
            self.flag_combo.setItemData(index, tip, Qt.ItemDataRole.ToolTipRole)
        self.flag_combo.currentIndexChanged.connect(self._fill_table)
        self.code_combo = QComboBox()
        self.code_combo.currentIndexChanged.connect(self._fill_table)
        self.part_combo = QComboBox()
        self.part_combo.currentIndexChanged.connect(self._fill_table)
        reload_button = QPushButton(self.tr("Aktualisieren"))
        reload_button.clicked.connect(self.refresh)
        filters = QHBoxLayout()
        filters.addWidget(self.search, 2)
        filters.addWidget(self.flag_combo, 1)
        filters.addWidget(self.code_combo, 1)
        filters.addWidget(self.part_combo, 1)
        filters.addWidget(reload_button)

        self.summary = hint_label()
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([self.tr(c) for c in COLUMNS])
        for column, tip in enumerate(COLUMN_TIPS):
            if tip:
                self.table.horizontalHeaderItem(column).setToolTip(self.tr(tip))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()  # Zeilennummern: die ID steht schon in Spalte 1
        header = self.table.horizontalHeader()
        for column, width in enumerate(WIDTHS):
            if width:
                self.table.setColumnWidth(column, width)
            else:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        # Ohne Sortierindikator bleibt die Datenreihenfolge; sortiert wird erst auf Klick.
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.detail = QTextBrowser()
        self._show_detail()  # Hinweis, solange keine Einheit gewählt ist
        self.show_raw = QCheckBox(self.tr("Roh-Antworten zeigen"))
        self.show_raw.setToolTip(
            self.tr("Die Antworten der Modelle im Wortlaut, auch auf Nachfragen (Repairs); "
                    "hilfreich, um ungültige Antworten zu verstehen.")
        )  # fmt: skip
        self.show_raw.toggled.connect(self._show_detail)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(filters)
        left_layout.addWidget(self.summary)
        left_layout.addWidget(self.table, 1)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.detail, 1)
        right_layout.addWidget(self.show_raw)
        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([970, 450])  # Anteile: genug Platz für die Tabellen im Detail
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

    # --- Daten laden ---------------------------------------------------------------------------

    def refresh(self) -> None:
        if self.project is None:
            return
        cfg = self.project.config
        try:
            _, items = prepare_prompts(self.project)
        except Exception as exc:
            self._items, self._results, self._calls = {}, {}, {}
            self.summary.setText(self.tr("Noch keine Ergebnisse: {0}").format(error_text(exc)))
            self._fill_table()
            return
        self._items = {i.item_id: i for i in items}
        self._results = results(self.project, list(self._items)) if cfg.panel else {}
        self._calls = {}
        basis = result_basis(self.project) if cfg.panel else None
        for key, config_hash in basis.runs.items() if basis else []:
            # Ohne Roh-Antworten: Die Detailansicht lädt sie für die gewählte Einheit nach.
            for call in self.project.store.calls(
                model_key=key, config_hash=config_hash, with_raw=False
            ):
                self._calls.setdefault(call.item_id, []).append(call)
        name, self._reference = reference_labels(self.project, items)
        self._hidden = hidden_test_items(self.project)
        self._parts = self.project.store.split()
        parts = [(self.tr("Dev und Test"), None), (self.tr("nur Dev"), "dev")]
        if not self._hidden:
            parts.append((self.tr("nur Test"), "test"))
        self.part_combo.blockSignals(True)
        fill_combo(self.part_combo, parts, self.part_combo.currentData() if self._parts else None)
        self.part_combo.blockSignals(False)
        self.part_combo.setEnabled(bool(self._parts))
        self.table.horizontalHeaderItem(3).setToolTip(
            self.tr("Referenz: {0}. Test-Gold bleibt bis zum Freeze verborgen.").format(name or "–")
        )
        codes = cfg.codebook.allowed_ids() if cfg.codebook else []
        fill_combo(
            self.code_combo,
            [(self.tr("Alle Codes"), None), *((c, c) for c in codes)],
            self.code_combo.currentData(),
        )
        if basis is None or not basis.runs:
            self.summary.setText(self.tr("Noch nichts kodiert: im Tab Lauf den Pilot starten."))
        else:
            done = sum(1 for r in self._results.values() if r.status == "ok")
            summary = self.tr("{0} von {1} Einheiten mit gültigem Ergebnis.").format(
                done, len(self._items)
            )
            warnings = [localized(w, common.UI_LANGUAGE) for w in basis_warnings(self.project)]
            self.summary.setText("\n".join([summary, *warnings]))
        self._fill_table()

    def _visible(self) -> list[str]:
        needle = self.search.text().strip().casefold()
        flag = self.flag_combo.currentData()
        code = self.code_combo.currentData()
        part = self.part_combo.currentData()
        visible = []
        for item_id, item in self._items.items():
            result = self._results.get(item_id)
            if part and self._parts.get(item_id) != part:
                continue
            if flag == "view:deviates" and not self._deviates(item_id, result):
                continue
            if flag == "view:unsure" and not (
                result is not None and self.project and unsure(result, self.project.config.review)
            ):
                continue
            if needle and needle not in item_id.casefold() and needle not in item.target.casefold():
                continue
            if flag == "status:bad" and (
                result is None or result.status not in ("invalid", "error")
            ):
                continue
            if flag == "status:pending" and result is not None and result.status != "pending":
                continue
            if (
                flag
                and not flag.startswith(("status:", "view:"))
                and (result is None or flag not in result.flags)
            ):
                continue
            if code and (result is None or code not in result.final):
                continue
            visible.append(item_id)
        return visible

    def _fill_table(self) -> None:
        selected = self._selected_id()
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        visible = self._visible()
        self.table.setRowCount(len(visible))
        for row, item_id in enumerate(visible):
            item, result = self._items[item_id], self._results.get(item_id)
            top = result.ranking[0] if result and result.ranking else None
            values = [
                item_id,
                item.target.replace("\n", " ⏎ ")[:200],
                ", ".join(result.final) if result else "",
                self._reference_text(item_id),
                f"{top.p_sc:.2f}" if top else "",
                f"{top.p_verbal:.2f}" if top else "",
                ", ".join(s.code for s in result.ranking[1:]) if result else "",
                self._flags_text(result.flags) if result else "",
                status_text(result.status if result else "pending"),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item_id)
                if column == HINTS_COLUMN and value:
                    cell.setToolTip(value)  # schmale Spalte: ganz lesbar beim Darüberfahren
                self.table.setItem(row, column, cell)
            if item_id == selected:
                self.table.selectRow(row)
        self.table.setUpdatesEnabled(True)
        self.table.setSortingEnabled(True)

    def _flags_text(self, flags: list[str]) -> str:
        return ", ".join(self.tr(FLAG_LABELS.get(f, f)) for f in flags)

    def _reference_text(self, item_id: str) -> str:
        if item_id in self._hidden:
            return self.tr("verborgen (Test)")
        label = self._reference.get(item_id)
        if label is None:
            return ""
        return ", ".join(sorted(label)) if isinstance(label, frozenset) else str(label)

    def _deviates(self, item_id: str, result: ItemResult | None) -> bool:
        """LLM weicht von der sichtbaren Referenz ab (Test vor dem Freeze zählt nie)."""
        label = self._reference.get(item_id)
        if item_id in self._hidden or label is None or result is None or result.status != "ok":
            return False
        llm = frozenset(result.final) if isinstance(label, frozenset) else result.final[0]
        return llm != label

    def _selected_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].data(Qt.ItemDataRole.UserRole) if rows else None

    # --- Detail --------------------------------------------------------------------------------

    def _show_detail(self) -> None:
        item_id = self._selected_id()
        if item_id is None or item_id not in self._items:
            hint = self.tr(
                "Eine Einheit in der Tabelle anklicken: Hier erscheinen der Text mit markierten "
                "Signalwörtern, die Begründung und alle Durchläufe."
            )
            self.detail.setHtml(f"<p style='color:gray'>{escape(hint)}</p>")
            return
        self.detail.setHtml(self.detail_html(item_id))

    def detail_html(self, item_id: str) -> str:
        item = self._items[item_id]
        result = self._results.get(item_id)
        parts = [f"<h3>{escape(item_id)}</h3>"]
        if item.before:
            parts.append(self._context(item.before))
        parts.append(f"<p style='font-size:11pt'>{_highlight(item.target, result)}</p>")
        if item.after:
            parts.append(self._context(item.after))
        if result is None or result.status != "ok":
            status = result.status if result else "pending"
            parts.append(
                "<p><b>" + escape(self.tr("Status")) + f":</b> {escape(status_text(status))}</p>"
            )
        else:
            parts.append(self._candidates(result))
            caveat = self.tr("LLM-Plausibilisierung, keine Erklärung der Entscheidung")
            parts.append(
                "<p><b>" + escape(self.tr("Begründung")) + f"</b> <i>({escape(caveat)})</i>"
                f":<br>{escape(result.rationale or '')}</p>"
            )
            if result.votes:
                votes = "<br>".join(
                    f"{escape(m)}: {escape(', '.join(v))}" for m, v in result.votes.items()
                )
                parts.append(
                    "<p><b>" + escape(self.tr("Stimmen je Modell")) + f"</b><br>{votes}</p>"
                )
            if result.flags:
                parts.append(
                    "<p><b>"
                    + escape(self.tr("Hinweise"))
                    + f":</b> {escape(self._flags_text(result.flags))}</p>"
                )
        light = self._calls.get(item_id, [])
        store = self.project.store if self.project else None
        calls = light
        if self.show_raw.isChecked() and store is not None:
            # Roh-Antworten nur auf Wunsch nachladen: bei Reasoning-Modellen oft sehr lang.
            runs = sorted({(c.model_key, c.config_hash) for c in light})
            calls = [
                c
                for key, config_hash in runs
                for c in store.calls(model_key=key, config_hash=config_hash, item_id=item_id)
            ]
        parts.append(self._runs(calls))
        return "".join(parts)

    @staticmethod
    def _context(lines: tuple[Line, ...]) -> str:
        rendered = "<br>".join(
            escape(f"[{ln.unit_id}] {ln.speaker + ': ' if ln.speaker else ''}{ln.text}")
            for ln in lines
        )
        return f"<p style='color:gray'>{rendered}</p>"

    def _candidates(self, result: ItemResult) -> str:
        rows = []
        for rank, score in enumerate(result.ranking):
            color = (
                HIGHLIGHT[rank]
                if score.code in result.final and rank < len(HIGHLIGHT)
                else "#e5e7eb"
            )
            spans = result.evidence.get(score.code, [])
            evidence = ", ".join(
                f"„{escape(s.text)}“ ({self.tr(STATUS_NOTE.get(s.status, s.status))}, {s.count}×)"
                for s in spans
            )
            rows.append(
                f"<tr><td style='background:{color};color:{DARK_TEXT}'>"
                f"<b>{escape(score.code)}</b></td>"
                f"<td>{score.p_sc:.2f}</td><td>{score.p_verbal:.2f}</td><td>{evidence}</td></tr>"
            )
        columns = (
            self.tr("Code"),
            self.tr("Konsistenz (p_sc)"),
            self.tr("Selbsteinschätzung (p_verbal)"),
            self.tr("Signalwörter"),
        )
        header = "<tr>" + "".join(f"<th>{escape(c)}</th>" for c in columns) + "</tr>"
        legend = self.tr(
            "Farbig: die LLM-Codes, ihre Signalwörter im Text in derselben Farbe; grau: "
            "Alternativen. In Klammern: wie das Signalwort im Text gefunden wurde und in wie "
            "vielen Durchläufen es genannt wurde (3× = in drei)."
        )
        return (
            f"<table cellpadding='4' border='0'>{header}{''.join(rows)}</table>"
            f"<p><small>{escape(legend)}</small></p>"
        )

    def _runs(self, calls: list[CallRecord]) -> str:
        if not calls:
            return ""
        columns = (
            self.tr("Modell"),
            self.tr("Durchlauf"),
            self.tr("Status"),
            self.tr("Code"),
            self.tr("Hinweise"),
        )
        legend = self.tr(
            "Code: der erste Code der Antwort, in Klammern die Selbsteinschätzung des Modells "
            "(0–100)."
        )
        rows = ["<tr>" + "".join(f"<th>{escape(c)}</th>" for c in columns) + "</tr>"]
        for call in sorted(calls, key=lambda c: (c.model_key, c.run)):
            candidates = (call.answer or {}).get("candidates", [])
            top = (
                f"{candidates[0]['code']} ({candidates[0]['confidence']:.0f})" if candidates else ""
            )
            note = call.error or self._flags_text(call.flags)
            rows.append(
                f"<tr><td>{escape(call.model_key)}</td><td>{call.run + 1}</td>"
                f"<td>{escape(status_text(call.status))}</td><td>{escape(top)}</td>"
                f"<td>{escape(note)}</td></tr>"
            )
        html = (
            heading("h4", self.tr("Durchläufe"))
            + f"<table cellpadding='3'>{''.join(rows)}</table>"
            + f"<p><small>{escape(legend)}</small></p>"
        )
        if not self.show_raw.isChecked():
            return html
        run_label = self.tr("Durchlauf")
        answer, repair = self.tr("Antwort"), self.tr("Antwort auf die Nachfrage (Repair)")
        raw = "".join(
            f"<p><small>{escape(c.model_key)} · {escape(run_label)} {c.run + 1} · "
            f"{escape(answer if i == 0 else repair)}</small>"
            f"<pre style='white-space:pre-wrap'>{escape(text[:2000])}</pre></p>"
            for c in sorted(calls, key=lambda c: (c.model_key, c.run))
            for i, text in enumerate(c.raw)
        )
        return html + heading("h4", self.tr("Roh-Antworten")) + raw


def _highlight(text: str, result: ItemResult | None) -> str:
    """Signalwörter der finalen Codes farbig markieren; überlappende Spans werden übersprungen."""
    spans: list[tuple[int, int, str]] = []
    if result is not None:
        for rank, code in enumerate(result.final):
            color = HIGHLIGHT[rank % len(HIGHLIGHT)]
            for span in result.evidence.get(code, []):
                if span.start is not None and span.end is not None:
                    spans.append((span.start, span.end, color))
    out, position = [], 0
    for start, end, color in sorted(spans):
        if start < position:
            continue
        out.append(escape(text[position:start]))
        out.append(
            f"<span style='background:{color};color:{DARK_TEXT}'>{escape(text[start:end])}</span>"
        )
        position = end
    out.append(escape(text[position:]))
    return "".join(out).replace("\n", "<br>")
