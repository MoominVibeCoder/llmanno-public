"""Gold-Werte den Codes zuordnen; im Tab Codebuch und in der Evaluation."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from llmanno.core.gold import gold_columns, gold_values
from llmanno.core.project import Project
from llmanno.gui.common import error_text, fill_combo, hint_label

IGNORE = "__ignore__"


class GoldMappingBox(QGroupBox):
    """Rohwerte der Gold-Spalten, die keinem Code entsprechen, samt den schon festgelegten.

    Ohne Zuordnung zählen solche Werte als fehlend: Die Einheit fehlt im Split und in jedem
    Vergleich. `changed` meldet eine geänderte Konfiguration."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__(self.tr("Gold-Werte zuordnen"))
        self.project: Project | None = None
        self.separator = QLineEdit()
        self.separator.setMaximumWidth(60)
        self.separator.editingFinished.connect(self._apply_separator)
        self.info = hint_label()
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            [self.tr("Gold-Wert"), self.tr("Anzahl"), self.tr("Code")]
        )
        self.table.setMaximumHeight(180)
        self.table.horizontalHeader().setStretchLastSection(True)
        header_tips = (
            self.tr("Wert aus den Gold-Spalten, der keinem Kürzel der Code-Liste entspricht, "
                    "etwa ein ausgeschriebener Name oder ein Tippfehler."),
            self.tr("Wie oft der Wert in den Gold-Spalten vorkommt."),
            self.tr("Welchem Code der Wert entspricht. „ignorieren“: bewusst kein Code (etwa "
                    "„unklar“), zählt als fehlend, ohne weitere Warnung. „nicht zugeordnet“: noch "
                    "offen, zählt ebenfalls als fehlend und bleibt als Warnung stehen."),
        )  # fmt: skip
        for column, tip in enumerate(header_tips):
            self.table.horizontalHeaderItem(column).setToolTip(tip)
        form = QFormLayout()
        form.addRow(self.tr("Trennzeichen Multi-Label"), self.separator)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.info)
        layout.addWidget(self.table)

    def refresh(self, project: Project | None) -> None:
        self.project = project
        self.table.setRowCount(0)
        if project is None:
            return
        cfg = project.config
        self.separator.blockSignals(True)
        self.separator.setText(cfg.gold.separator)
        self.separator.blockSignals(False)
        multi = cfg.mode == "multi"
        self.separator.setEnabled(multi)
        self.separator.setToolTip(
            self.tr("Trennt mehrere Codes in einer Gold-Zelle, etwa „K1;K3“.")
            if multi
            else self.tr("Nur bei mehreren Codes je Einheit (Multi-Label, Tab Projekt).")
        )
        if not gold_columns(cfg):
            self.info.setText(self.tr("Keine Gold-Spalten zugeordnet (Tab Daten)."))
            return
        if cfg.codebook is None or not cfg.codebook.allowed_ids():
            self.info.setText(self.tr("Zuordnen lässt sich erst, wenn die Code-Liste steht."))
            return
        found, unmapped = gold_values(cfg, project.store.units())
        value_map = cfg.gold.value_map
        rows = sorted(
            (v for v in found if v in unmapped or v in value_map), key=lambda v: (-found[v], v)
        )
        if unmapped:
            self.info.setText(
                self.tr("{0} Gold-Werte ohne Code ({1} Vorkommen): Sie zählen als fehlend, die "
                        "Einheiten fehlen im Split und in den Vergleichen.").format(
                    len(unmapped), sum(unmapped.values())
                )
            )  # fmt: skip
        else:
            self.info.setText(self.tr("Alle Gold-Werte entsprechen einem Code."))
        codes = cfg.codebook.allowed_ids()
        for value in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(value))
            self.table.setItem(row, 1, QTableWidgetItem(str(found[value])))
            combo = QComboBox()
            current = (
                (IGNORE if value_map[value] is None else value_map[value])
                if (value in value_map)
                else None
            )
            fill_combo(
                combo,
                [
                    (self.tr("— nicht zugeordnet —"), None),
                    (self.tr("ignorieren"), IGNORE),
                    *((c, c) for c in codes),
                ],
                current,
            )
            combo.currentIndexChanged.connect(
                lambda _i, v=value, c=combo: self._map(v, c.currentData())
            )
            self.table.setCellWidget(row, 2, combo)

    def _map(self, raw: str, target: str | None) -> None:
        assert self.project is not None
        value_map = dict(self.project.config.gold.value_map)
        if target is None:
            value_map.pop(raw, None)
        else:
            value_map[raw] = None if target == IGNORE else target
        self.project.update(
            gold=self.project.config.gold.model_copy(update={"value_map": value_map})
        )
        # Erst nach dem Signal der Combo neu aufbauen: Sie gehört zur Tabelle.
        QTimer.singleShot(0, self._changed)

    def _apply_separator(self) -> None:
        if self.project is None:
            return
        gold = self.project.config.gold
        try:
            self.project.update(
                gold=gold.model_copy(update={"separator": self.separator.text() or ";"})
            )
        except Exception as exc:
            self.info.setText(error_text(exc))
            return
        self._changed()

    def _changed(self) -> None:
        self.refresh(self.project)
        self.changed.emit()
