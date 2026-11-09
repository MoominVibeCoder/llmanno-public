"""Tab „Modelle“: Anbieter verwalten, Verbindung und Fähigkeiten prüfen, Panel zusammenstellen."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from llmanno.core import providers
from llmanno.core.config import (
    GWDG_CHAT_AI,
    KI_CONNECT_NRW,
    OLLAMA_LOCAL,
    Capabilities,
    ModelConfig,
    ProviderConfig,
)
from llmanno.core.providers.base import ModelInfo, ProviderError
from llmanno.core.providers.ollama import OllamaProvider
from llmanno.gui.common import (
    Collapsible,
    Page,
    Task,
    error_label,
    error_text,
    fill_combo,
    hint_label,
)

PANEL_COLUMNS = 5
EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


class TemperatureBox(QDoubleSpinBox):
    """Temperatur mit zwei Sonderwerten: nicht senden (Anbieter legt fest) und automatisch."""

    PROVIDER, AUTO = -0.2, -0.1

    def __init__(self) -> None:
        super().__init__()
        self.labels = {self.PROVIDER: self.tr("nicht senden"), self.AUTO: self.tr("automatisch")}
        self.setRange(self.PROVIDER, 2.0)
        self.setSingleStep(0.1)

    def set_model(self, model: ModelConfig) -> None:
        if model.provider_temperature:
            self.setValue(self.PROVIDER)
        else:
            self.setValue(self.AUTO if model.temperature is None else model.temperature)

    def changes(self) -> dict:
        value = round(self.value(), 2)
        return {
            "temperature": None if value < 0 else value,
            "provider_temperature": value == self.PROVIDER,
        }

    def textFromValue(self, value: float) -> str:  # noqa: N802 (Qt)
        return self.labels.get(round(value, 1)) or super().textFromValue(value)

    def valueFromText(self, text: str) -> float:  # noqa: N802 (Qt)
        for value, label in self.labels.items():
            if text == label:
                return value
        return super().valueFromText(text)

    def validate(self, text: str, pos: int) -> object:
        if text in self.labels.values():
            return QValidator.State.Acceptable, text, pos
        if any(label.startswith(text) for label in self.labels.values()):
            return QValidator.State.Intermediate, text, pos
        return super().validate(text, pos)


def _list_models(config: ProviderConfig) -> list[ModelInfo]:
    provider = providers.make_provider(config)
    try:
        return provider.list_models()
    finally:
        provider.close()


class ModelsPage(Page):
    help_topics = (
        "4. Modelle",
        "Akademische APIs: GWDG/KISSKI und KI:connect.NRW",
        "Läufe, Temperatur und Panel",
        "Kosten und Kontingent",
    )

    def __init__(self) -> None:
        super().__init__()
        self._models: dict[str, list[ModelInfo]] = {}  # Anbieter-Schlüssel → zuletzt geladen
        self._task: Task | None = None

        self.provider_list = QListWidget()
        self.provider_list.currentRowChanged.connect(self._show_provider)
        add_ollama = QPushButton(self.tr("+ Ollama (lokal)"))
        add_ollama.clicked.connect(lambda: self._add_provider(OLLAMA_LOCAL))
        add_gwdg = QPushButton(self.tr("+ GWDG/KISSKI"))
        add_gwdg.clicked.connect(lambda: self._add_provider(GWDG_CHAT_AI))
        add_kiconnect = QPushButton(self.tr("+ KI:connect.NRW"))
        add_kiconnect.clicked.connect(lambda: self._add_provider(KI_CONNECT_NRW))
        add_custom = QPushButton(self.tr("+ Eigener Anbieter"))
        add_custom.clicked.connect(self._add_custom)
        self.remove_provider_button = QPushButton(self.tr("Entfernen"))
        self.remove_provider_button.setToolTip(
            self.tr("Gewählten Anbieter samt seinen Modellen im Panel entfernen.")
        )
        self.remove_provider_button.clicked.connect(self._remove_provider)
        provider_buttons = QHBoxLayout()
        for button in (
            add_ollama,
            add_gwdg,
            add_kiconnect,
            add_custom,
            self.remove_provider_button,
        ):
            provider_buttons.addWidget(button)

        self.name_edit = QLineEdit()
        self.kind_combo = QComboBox()
        fill_combo(
            self.kind_combo, [("Ollama", "ollama"), (self.tr("OpenAI-kompatibel"), "openai")]
        )
        self.url_edit = QLineEdit()
        self.auth_combo = QComboBox()
        fill_combo(
            self.auth_combo,
            [
                (self.tr("keine"), "none"),
                (self.tr("Bearer-Token"), "bearer"),
                (self.tr("api-key-Header"), "api-key"),
            ],
        )
        self.privacy_combo = QComboBox()
        fill_combo(
            self.privacy_combo,
            [
                (self.tr("lokal: Daten verlassen das Gerät nicht"), "local"),
                (self.tr("akademisch / EU"), "academic"),
                (self.tr("extern"), "external"),
            ],
        )
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(0, 100000)
        self.rate_spin.setSpecialValueText(self.tr("unbegrenzt"))
        self.rate_spin.setSuffix(self.tr(" / min"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 3600)
        self.timeout_spin.setSuffix(" s")
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 32)
        tips = {
            self.kind_combo: self.tr("Ollama für lokale Modelle; OpenAI-kompatibel für fast alle "
                                     "anderen (GWDG/KISSKI, KI:connect.NRW, vLLM, LM Studio, "
                                     "OpenAI)."),
            self.url_edit: self.tr("Adresse der Schnittstelle, bei OpenAI-kompatiblen Anbietern "
                                   "meist mit /v1 am Ende. Die Vorlagen sind schon richtig "
                                   "eingetragen."),
            self.auth_combo: self.tr("Wie der API-Key mitgeschickt wird. „Bearer-Token“ passt "
                                     "für die meisten Anbieter, „api-key-Header“ etwa für "
                                     "Azure."),
            self.privacy_combo: self.tr("Wohin die Texte gehen. Bei allen Stufen außer „lokal“ "
                                        "fragt das Programm vor dem Senden nach Zustimmung; die "
                                        "Stufe steht im Report."),
            self.rate_spin: self.tr("Höchstens so viele Anfragen pro Minute. Nur setzen, wenn "
                                    "der Anbieter eine Grenze nennt; 0 heißt unbegrenzt."),
            self.timeout_spin: self.tr("So lange wartet das Programm auf eine Antwort, dann "
                                       "versucht es die Anfrage erneut. Bei großen oder denkenden "
                                       "Modellen erhöhen."),
            self.parallel_spin: self.tr("Wie viele Anfragen je Modell gleichzeitig laufen. Mehr "
                                        "geht schneller, aber nur, wenn der Anbieter es erlaubt "
                                        "(Ollama: Einstellung OLLAMA_NUM_PARALLEL); sonst lehnt er "
                                        "Anfragen ab. Manche Server antworten dann minimal "
                                        "anders; der Report nennt den Wert."),
        }  # fmt: skip
        for widget, tip in tips.items():
            widget.setToolTip(tip)
        for widget in (self.name_edit, self.url_edit):
            widget.editingFinished.connect(self._apply_provider)
        for combo in (self.kind_combo, self.auth_combo, self.privacy_combo):
            combo.currentIndexChanged.connect(self._apply_provider)
        for spin in (self.rate_spin, self.timeout_spin, self.parallel_spin):
            spin.editingFinished.connect(self._apply_provider)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText(self.tr("API-Key eingeben"))
        self.key_edit.setToolTip(
            self.tr("Ein eingetragener Key wird spätestens beim Testen der Verbindung im "
                    "Schlüsselbund gespeichert.")
        )  # fmt: skip
        self.key_button = QPushButton(self.tr("Im Schlüsselbund speichern"))
        self.key_button.setToolTip(
            self.tr("Speichert den Key im Schlüsselbund des Betriebssystems, nicht im "
                    "Projektordner. Er gilt für alle Projekte mit derselben Adresse.")
        )  # fmt: skip
        self.key_button.clicked.connect(self._save_key)
        self.remove_key_button = QPushButton(self.tr("Key entfernen"))
        self.remove_key_button.clicked.connect(self._remove_key)
        self.key_status = hint_label()
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.key_button)
        key_row.addWidget(self.remove_key_button)

        self.caps_label = hint_label()
        self.caps_label.setToolTip(
            self.tr("Festes Antwortformat: Der Anbieter erzwingt die Form der Antwort "
                    "(JSON-Schema; „teilweise“: nur JSON-Objekt). Wiederholbarkeit: Er nimmt einen "
                    "Startwert (Seed) an. Ohne Prüfung versucht das Programm das Beste.")
        )  # fmt: skip
        self.test_button = QPushButton(self.tr("Verbindung testen"))
        self.test_button.setToolTip(
            self.tr("Fragt beim Anbieter die Liste der Modelle ab; danach lassen sie sich unten "
                    "auswählen.")
        )  # fmt: skip
        self.test_button.clicked.connect(self._test_connection)
        self.probe_button = QPushButton(self.tr("Fähigkeiten prüfen"))
        self.probe_button.setToolTip(
            self.tr("Prüft mit wenigen kurzen Anfragen an das unten gewählte Modell, ob der "
                    "Anbieter ein festes Antwortformat und Wiederholbarkeit unterstützt. Vor dem "
                    "ersten Lauf mit einem OpenAI-kompatiblen Anbieter sinnvoll.")
        )  # fmt: skip
        self.probe_button.clicked.connect(self._probe)
        check_row = QHBoxLayout()
        check_row.addWidget(self.test_button)
        check_row.addStretch()

        form = QFormLayout()
        form.addRow(self.tr("Name"), self.name_edit)
        form.addRow(self.tr("API-Key"), key_row)
        form.addRow("", self.key_status)
        form.addRow(self.tr("Datenschutz"), self.privacy_combo)
        self.privacy_warning = error_label()
        form.addRow("", self.privacy_warning)
        form.addRow(check_row)
        self.provider_advanced = Collapsible()
        advanced = QFormLayout(self.provider_advanced.body)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.addRow(self.tr("Art"), self.kind_combo)
        advanced.addRow(self.tr("Basis-URL"), self.url_edit)
        advanced.addRow(self.tr("Authentifizierung"), self.auth_combo)
        advanced.addRow(self.tr("Rate-Limit"), self.rate_spin)
        advanced.addRow(self.tr("Zeitlimit"), self.timeout_spin)
        advanced.addRow(self.tr("Parallele Anfragen"), self.parallel_spin)
        advanced.addRow(self.tr("Fähigkeiten"), self.caps_label)
        advanced.addRow("", self.probe_button)
        self.provider_form = QGroupBox(self.tr("Anbieter"))
        provider_layout = QVBoxLayout(self.provider_form)
        provider_layout.addLayout(form)
        provider_layout.addWidget(self.provider_advanced)
        provider_layout.addStretch()

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(260)
        self.model_combo.lineEdit().setPlaceholderText(
            self.tr("Modellname eintragen oder erst „Verbindung testen“ für die Liste")
        )
        self.add_model_button = QPushButton(self.tr("Ins Panel übernehmen"))
        self.add_model_button.clicked.connect(self._add_to_panel)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.add_model_button)

        self.notes = hint_label()
        self.error = error_label()

        self.panel_table = QTableWidget(0, PANEL_COLUMNS)
        self.panel_table.setHorizontalHeaderLabels(
            [
                self.tr("Modell"),
                self.tr("Temperatur"),
                self.tr("Max. Tokens"),
                self.tr("Thinking"),
                self.tr("Reasoning-Aufwand"),
            ]
        )
        header_tips = (
            self.tr("Anbieter/Modell"),
            self.tr("Wie stark die Antworten streuen. „automatisch“: 0,7 bei mehreren "
                    "Durchläufen (für die Konsistenz), sonst 0. „nicht senden“, wenn der Anbieter "
                    "die Temperatur festlegt."),
            self.tr("Höchstlänge der Antwort in Token. Denkende Modelle (gpt-oss, GPT-5, Qwen3 …) "
                    "brauchen 4096 oder mehr, sonst bricht die Antwort ab und zählt als "
                    "ungültig."),
            self.tr("Nur Ollama: ob ein denkendes Modell vor der Antwort nachdenkt. „aus“ ist "
                    "schneller; „nicht senden“, wenn das Modell es nicht kann."),
            self.tr("Nur OpenAI-kompatible Anbieter: wie lange ein denkendes Modell nachdenkt, "
                    "etwa low, medium, high. „nicht senden“: Vorgabe des Anbieters."),
        )  # fmt: skip
        for column, tip in enumerate(header_tips):
            self.panel_table.horizontalHeaderItem(column).setToolTip(tip)
        header = self.panel_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, PANEL_COLUMNS):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.panel_empty = hint_label(
            self.tr("Noch kein Modell im Panel. 1. Oben einen Anbieter wählen oder hinzufügen. "
                    "2. „Verbindung testen“ lädt seine Modelle. 3. Ein Modell wählen und „Ins "
                    "Panel übernehmen“. Für den Einstieg reicht ein Modell.")
        )  # fmt: skip
        self.remove_model_button = QPushButton(self.tr("Aus dem Panel entfernen"))
        self.remove_model_button.clicked.connect(self._remove_from_panel)
        # Temperatur, Thinking und Reasoning-Aufwand: Spalten nur unter „Erweitert“.
        self.panel_advanced = Collapsible(
            self.tr("Erweitert: Temperatur, Thinking, Reasoning-Aufwand")
        )
        QVBoxLayout(self.panel_advanced.body).addWidget(
            hint_label(
                self.tr(
                    "Die Vorgaben passen meist. „nicht senden“ bei der Temperatur, wenn der "
                    "Anbieter sie festlegt (siehe seine Modellübersicht); der Report nennt "
                    "dann die Anbietervorgabe."
                )
            )  # fmt: skip
        )
        self.panel_advanced.toggle.toggled.connect(self._show_panel_columns)
        self._show_panel_columns(self.panel_advanced.toggle.isChecked())
        panel_box = QGroupBox(self.tr("Panel (alle Modelle kodieren dieselben Einheiten)"))
        panel_layout = QVBoxLayout(panel_box)
        panel_layout.addLayout(model_row)
        panel_layout.addWidget(self.panel_empty)
        panel_layout.addWidget(self.panel_table)
        panel_layout.addWidget(self.remove_model_button)
        panel_layout.addWidget(self.panel_advanced)
        panel_layout.addWidget(
            hint_label(
                self.tr(
                    "Modelle verschiedener Hersteller mischen: Modelle einer Familie irren "
                    "oft an denselben Stellen."
                )
            )  # fmt: skip
        )

        left = QVBoxLayout()
        left.addWidget(self.provider_list, 1)
        left.addLayout(provider_buttons)
        providers_row = QHBoxLayout()
        providers_row.addLayout(left, 1)
        providers_row.addWidget(self.provider_form, 2)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addLayout(providers_row)
        top_layout.addWidget(self.notes)
        top_layout.addWidget(self.error)

        # Ein Splitter hält die Mindesthöhe des Anbieter-Formulars ein, auch mit „Erweitert“.
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(panel_box)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

    # --- Stand anzeigen ------------------------------------------------------------------------

    def refresh(self) -> None:
        if self.project is None:
            return
        current = self.provider_list.currentRow()
        self.provider_list.blockSignals(True)
        self.provider_list.clear()
        names = [p.name for p in self.project.config.providers]
        for p in self.project.config.providers:
            # Den internen Schlüssel nur zeigen, wo zwei Anbieter gleich heißen.
            item = QListWidgetItem(f"{p.name}  ({p.key})" if names.count(p.name) > 1 else p.name)
            item.setToolTip(self.tr("{0}, Schlüssel im Projekt: {1}").format(p.base_url, p.key))
            self.provider_list.addItem(item)
        self.provider_list.blockSignals(False)
        count = self.provider_list.count()
        self.provider_list.setCurrentRow(min(max(current, 0), count - 1) if count else -1)
        self._show_provider()
        self._fill_panel()

    def _selected(self) -> ProviderConfig | None:
        row = self.provider_list.currentRow()
        if self.project is None or row < 0:
            return None
        return self.project.config.providers[row]

    def _show_provider(self) -> None:
        provider = self._selected()
        self.key_edit.clear()  # ein getippter Key gilt nur für den Anbieter, für den er war
        self.provider_form.setEnabled(provider is not None)
        self.remove_provider_button.setEnabled(provider is not None)
        self.add_model_button.setEnabled(provider is not None)
        if provider is None:
            return
        widgets = (self.name_edit, self.kind_combo, self.url_edit, self.auth_combo,
                   self.privacy_combo, self.rate_spin, self.timeout_spin,
                   self.parallel_spin)  # fmt: skip
        for widget in widgets:
            widget.blockSignals(True)
        self.name_edit.setText(provider.name)
        self.kind_combo.setCurrentIndex(self.kind_combo.findData(provider.kind))
        self.url_edit.setText(provider.base_url)
        self.auth_combo.setCurrentIndex(self.auth_combo.findData(provider.auth))
        self.privacy_combo.setCurrentIndex(self.privacy_combo.findData(provider.privacy))
        self.rate_spin.setValue(provider.rate_limit_per_min or 0)
        self.timeout_spin.setValue(int(provider.timeout_s))
        self.parallel_spin.setValue(provider.parallel)
        for widget in widgets:
            widget.blockSignals(False)
        self._show_key_status(provider)
        self.caps_label.setText(self._caps_text(provider.capabilities))
        self._show_privacy_warning(provider)
        names = [m.name for m in self._models.get(provider.key, [])]
        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems(names)
        if current and not names:
            self.model_combo.setEditText(current)

    def _show_key_status(self, provider: ProviderConfig) -> None:
        needs_key = provider.auth != "none"
        self.key_edit.setEnabled(needs_key)
        self.key_button.setEnabled(needs_key)
        self.remove_key_button.setEnabled(False)
        if not needs_key:
            self.key_status.setText(self.tr("Kein Key nötig."))
            return
        try:
            stored = providers.get_api_key(provider)
        except Exception as exc:  # kein Schlüsselbund-Backend verfügbar
            self.key_status.setText(self.tr("Schlüsselbund nicht verfügbar: {0}").format(exc))
            return
        self.remove_key_button.setEnabled(bool(stored))
        self.key_status.setText(
            self.tr("Key ist im Schlüsselbund hinterlegt.")
            if stored
            else self.tr("Noch kein Key hinterlegt.")
        )

    def _caps_text(self, caps: Capabilities) -> str:
        def word(value: bool | None) -> str:
            return {True: self.tr("ja"), False: self.tr("nein"), None: self.tr("ungeprüft")}[value]

        answer = self.tr("teilweise") if not caps.json_schema and caps.json_object else None
        return self.tr("Festes Antwortformat: {0} · Wiederholbarkeit: {1}").format(
            answer or word(caps.json_schema), word(caps.seed)
        )

    def _show_panel_columns(self, on: bool) -> None:
        for column in (1, 3, 4):
            self.panel_table.setColumnHidden(column, not on)

    def _fill_panel(self) -> None:
        assert self.project is not None
        self.panel_table.blockSignals(True)
        self.panel_table.setRowCount(0)
        for model in self.project.config.panel:
            row = self.panel_table.rowCount()
            self.panel_table.insertRow(row)
            name = QTableWidgetItem(model.key)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.panel_table.setItem(row, 0, name)
            temperature = TemperatureBox()
            temperature.set_model(model)
            temperature.valueChanged.connect(self._apply_panel)
            self.panel_table.setCellWidget(row, 1, temperature)
            tokens = QSpinBox()
            tokens.setRange(64, 32768)
            tokens.setSingleStep(128)
            tokens.setValue(model.max_tokens)
            tokens.valueChanged.connect(self._apply_panel)
            self.panel_table.setCellWidget(row, 2, tokens)
            thinking = QComboBox()
            fill_combo(
                thinking,
                [(self.tr("nicht senden"), None), (self.tr("aus"), False), (self.tr("an"), True)],
                model.thinking,
            )
            thinking.currentIndexChanged.connect(self._apply_panel)
            self.panel_table.setCellWidget(row, 3, thinking)
            effort = QComboBox()
            effort.setEditable(True)  # Anbieter nennen eigene Stufen
            effort.addItems([self.tr("nicht senden"), *EFFORTS])
            effort.setCurrentText(model.reasoning_effort or effort.itemText(0))
            openai = self.project.config.provider(model.provider).kind == "openai"
            effort.setEnabled(openai)
            effort.setToolTip(
                self.tr("Stufen laut Modellübersicht des Anbieters, etwa low/medium/high.")
                if openai
                else self.tr("Nur für OpenAI-kompatible Anbieter.")
            )
            effort.currentTextChanged.connect(self._apply_panel)
            self.panel_table.setCellWidget(row, 4, effort)
        self.panel_table.blockSignals(False)
        self.remove_model_button.setEnabled(bool(self.project.config.panel))
        self.panel_empty.setVisible(not self.project.config.panel)

    # --- Anbieter bearbeiten -------------------------------------------------------------------

    def _unique_key(self, base: str) -> str:
        assert self.project is not None
        keys = {p.key for p in self.project.config.providers}
        key, n = base, 2
        while key in keys:
            key, n = f"{base}-{n}", n + 1
        return key

    def _add_provider(self, preset: ProviderConfig) -> None:
        assert self.project is not None
        provider = preset.model_copy(update={"key": self._unique_key(preset.key)})
        self._update(providers=[*self.project.config.providers, provider])
        self.provider_list.setCurrentRow(self.provider_list.count() - 1)

    def _add_custom(self) -> None:
        self._add_provider(
            ProviderConfig(
                key="eigen",
                name=self.tr("Eigener Anbieter"),
                kind="openai",
                base_url="https://",
                auth="bearer",
                privacy="external",
            )  # fmt: skip
        )
        self.provider_advanced.toggle.setChecked(True)  # die Adresse fehlt noch

    def _remove_provider(self) -> None:
        provider = self._selected()
        if provider is None or self.project is None:
            return
        cfg = self.project.config
        affected = [m.key for m in cfg.panel if m.provider == provider.key]
        if affected:
            answer = QMessageBox.question(
                self,
                self.tr("Anbieter entfernen?"),
                self.tr("Mit „{0}“ verlassen auch diese Modelle das Panel: {1}. Trotzdem "
                        "entfernen?").format(provider.name, ", ".join(affected)),
            )  # fmt: skip
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._update(
            providers=[p for p in cfg.providers if p.key != provider.key],
            panel=[m for m in cfg.panel if m.provider != provider.key],
        )

    def _apply_provider(self) -> None:
        provider = self._selected()
        if provider is None or self.project is None:
            return
        kind = self.kind_combo.currentData()
        url = self.url_edit.text().strip()
        caps = provider.capabilities
        if kind != provider.kind or url != provider.base_url:
            # Anderer Server: frühere Prüfergebnisse gelten nicht mehr.
            caps = OLLAMA_LOCAL.capabilities if kind == "ollama" else Capabilities()
        updated = provider.model_copy(
            update={
                "name": self.name_edit.text().strip() or provider.name,
                "kind": kind,
                "base_url": url,
                "auth": self.auth_combo.currentData(),
                "privacy": self.privacy_combo.currentData(),
                "rate_limit_per_min": self.rate_spin.value() or None,
                "timeout_s": float(self.timeout_spin.value()),
                "parallel": self.parallel_spin.value(),
                "capabilities": caps,
            }
        )
        if updated == provider:
            return
        providers_list = [
            updated if p.key == provider.key else p for p in self.project.config.providers
        ]
        self._update(providers=providers_list)
        self._show_privacy_warning(updated)

    def _show_privacy_warning(self, provider: ProviderConfig) -> None:
        lines = []
        if provider.privacy == "local" and not provider.on_this_machine:
            lines.append(
                self.tr("Die Adresse ist nicht dieser Rechner. Das Programm behandelt den "
                        "Anbieter deshalb als extern: Vor dem Senden fragt es nach Zustimmung, "
                        "der Report nennt „externe API“.")
            )  # fmt: skip
        if provider.key_in_clear:
            lines.append(
                self.tr("Der API-Schlüssel ginge unverschlüsselt (http) über das Netz. Wenn "
                        "möglich https verwenden.")
            )  # fmt: skip
        self.privacy_warning.setText("\n".join(lines))

    def _save_key(self) -> None:
        provider = self._selected()
        key = self.key_edit.text().strip()
        if provider is None or not key:
            return
        try:
            providers.set_api_key(provider, key)
        except Exception as exc:
            self.error.setText(self.tr("Key nicht gespeichert: {0}").format(exc))
            return
        self.key_edit.clear()
        self._log("api_key_set", provider=provider.key, base_url=provider.base_url)
        self._show_key_status(provider)

    def _remove_key(self) -> None:
        provider = self._selected()
        if provider is None:
            return
        answer = QMessageBox.question(
            self,
            self.tr("API-Key entfernen?"),
            self.tr(
                "Der Key für {0} wird aus dem Schlüsselbund gelöscht. Er gilt für die Adresse "
                "{1}; andere Projekte mit derselben Adresse verlieren ihn ebenfalls."
            ).format(provider.name, provider.base_url),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = providers.delete_api_key(provider)
        except Exception as exc:  # kein Schlüsselbund-Backend verfügbar
            self.error.setText(self.tr("Key nicht entfernt: {0}").format(exc))
            return
        if removed:
            self._log("api_key_removed", provider=provider.key, base_url=provider.base_url)
        self._show_key_status(provider)

    def _log(self, event: str, **details: object) -> None:
        if self.project:
            self.project.store.log(event, **details)

    # --- Prüfen im Hintergrund -----------------------------------------------------------------

    def _start(self, fn, on_done) -> None:
        if self._task and self._task.running:
            return
        kept = self.error.text()  # etwa „Key nicht gespeichert“ aus dem Testen davor
        self.error.setText(kept if kept.startswith(self.tr("Key nicht gespeichert")) else "")
        self.notes.setText(self.tr("Läuft …"))
        self.test_button.setEnabled(False)
        self.probe_button.setEnabled(False)
        # Nach einem Projektwechsel gehört das Ergebnis nicht ins neue Projekt, auch wenn
        # dort ein Anbieter gleich heißt.
        project, provider = self.project, self._selected()
        self._task = Task(self, lambda report: fn())
        self._task.done.connect(lambda result: self.project is project and on_done(result))
        self._task.failed.connect(
            lambda exc: self.project is project and self._failed(exc, provider)
        )
        self._task.finished.connect(self._task_finished)
        self._task.start()

    @property
    def busy(self) -> bool:
        return self._task is not None and self._task.running

    def _failed(self, exc: BaseException, provider: ProviderConfig | None) -> None:
        hint = self.connection_hint(exc, provider)
        message = error_text(exc)
        self.error.setText(
            hint + "\n" + self.tr("Meldung: {0}").format(message) if hint else message
        )
        self.notes.clear()

    def connection_hint(self, exc: BaseException, provider: ProviderConfig | None) -> str:
        """Klartext mit Lösungsweg für typische Fehler beim Verbindungstest."""
        if not isinstance(exc, ProviderError):
            return ""
        if exc.status in (401, 403):
            return self.tr("API-Key fehlt oder ist ungültig. Key oben eintragen und „Im "
                           "Schlüsselbund speichern“; bei Hochschul-Anbietern den Key aus "
                           "deren Portal verwenden.")  # fmt: skip
        if exc.status == 404:
            return self.tr("Adresse oder Modell nicht gefunden. Basis-URL prüfen (unter "
                           "„Erweitert“, OpenAI-kompatible Anbieter enden meist auf /v1) und "
                           "den Modellnamen.")  # fmt: skip
        if exc.status == 429:
            return self.tr("Der Anbieter lehnt gerade zu viele Anfragen ab (Kontingent). Etwas "
                           "warten und erneut versuchen.")  # fmt: skip
        if exc.status is None and exc.retryable:  # keine Verbindung oder Zeitüberschreitung
            if provider is not None and provider.kind == "ollama":
                return self.tr(
                    "Läuft Ollama? Ollama starten (Startmenü oder „ollama serve“) "
                    "und die Basis-URL prüfen, üblich ist http://localhost:11434."
                )
            return self.tr("Server nicht erreichbar. Internetverbindung und Basis-URL prüfen; "
                           "manche Anbieter sind nur im Hochschulnetz oder per VPN "
                           "erreichbar.")  # fmt: skip
        return ""

    def _save_typed_key(self) -> None:
        """Ein eingetippter, noch nicht gespeicherter Key gilt beim Testen gleich."""
        if self.key_edit.isEnabled() and self.key_edit.text().strip():
            self._save_key()

    def _task_finished(self) -> None:
        self.test_button.setEnabled(True)
        self.probe_button.setEnabled(True)

    def _test_connection(self) -> None:
        provider = self._selected()
        if provider is None:
            return
        self._save_typed_key()

        def done(models: list[ModelInfo]) -> None:
            self._models[provider.key] = models
            self.notes.setText(
                self.tr("Verbindung steht, {0} Modelle gefunden.").format(len(models))
            )
            self._show_provider()

        self._start(lambda: _list_models(provider), done)

    def _probe(self) -> None:
        provider = self._selected()
        model = self.model_combo.currentText().strip()
        if provider is None:
            return
        if not model:
            self.error.setText(self.tr("Zuerst unten ein Modell wählen oder eintragen."))
            return
        self._save_typed_key()

        def done(result: tuple[Capabilities, list[str]]) -> None:
            caps, notes = result
            assert self.project is not None
            updated = provider.model_copy(update={"capabilities": caps})
            self._update(
                providers=[
                    updated if p.key == provider.key else p for p in self.project.config.providers
                ]
            )
            self._log(
                "provider_probed",
                provider=provider.key,
                model=model,
                capabilities=caps.model_dump(),
            )
            self.notes.setText(
                self.tr("Geprüft mit {0}. {1}").format(model, " · ".join(notes))
                if notes
                else self.tr("Geprüft mit {0}.").format(model)
            )

        self._start(lambda: providers.probe(provider, model), done)

    # --- Panel ---------------------------------------------------------------------------------

    def _add_to_panel(self) -> None:
        provider = self._selected()
        name = self.model_combo.currentText().strip()
        if provider is None or self.project is None or not name:
            return
        info = next((m for m in self._models.get(provider.key, []) if m.name == name), None)
        if provider.kind == "ollama" and (info is None or not info.capabilities):
            # Ältere Ollama-Versionen nennen die Fähigkeiten nur in /api/show.
            try:
                ollama = OllamaProvider(provider.base_url, timeout=10)
                try:
                    info = ollama.show(name)
                finally:
                    ollama.close()
            except ProviderError:
                pass
        thinking = (
            False
            if provider.kind == "ollama" and info and "thinking" in info.capabilities
            else None
        )
        thinker = providers.is_thinking_model(name, info.capabilities if info else ())
        model = ModelConfig(
            provider=provider.key,
            model=name,
            thinking=thinking,
            **({"max_tokens": providers.THINKING_TOKENS} if thinker else {}),
        )
        notes = []
        if thinker:
            notes.append(
                self.tr("Max. Tokens auf {0} gesetzt: {1} ist ein denkendes Modell und braucht "
                        "Platz dafür.").format(providers.THINKING_TOKENS, name)
            )  # fmt: skip
        coding = self.project.config.coding
        changes: dict[str, object] = {"panel": [*self.project.config.panel, model]}
        size = providers.parameter_billions(info)
        small = size is not None and size < providers.SMALL_MODEL_BILLIONS
        if small and coding.evidence_repair and self.project.store.count_calls() == 0:
            changes["coding"] = coding.model_copy(update={"evidence_repair": False})
            notes.append(
                self.tr("{0} ist ein kleines Modell (≈ {1:g} Mrd. Parameter): Die Nachfrage bei "
                        "fehlenden Belegen (Repair) ist deshalb aus. Sie kostet dort einen "
                        "zweiten Aufruf und hilft selten; im Tab Prompt & Einheiten lässt sie "
                        "sich einschalten.")
                .format(name, round(size, 1))
            )  # fmt: skip
        elif small and coding.evidence_repair:
            # Nach Läufen nicht still umschalten: Der Repair gehört zur Lauf-Identität, alle
            # Modelle würden neu kodiert.
            notes.append(
                self.tr("{0} ist ein kleines Modell (≈ {1:g} Mrd. Parameter). Die Nachfrage bei "
                        "fehlenden Belegen (Repair) bleibt an, weil es schon Läufe gibt; "
                        "ausschalten (Tab Prompt & Einheiten) hieße, alle Modelle neu zu "
                        "kodieren.")
                .format(name, round(size, 1))
            )  # fmt: skip
        before = len(self.project.config.panel)
        self._update(**changes)
        if len(self.project.config.panel) > before:  # sonst steht der Fehler im Fehlerfeld
            self.notes.setText(" ".join(notes))

    def _remove_from_panel(self) -> None:
        if self.project is None:
            return
        rows = {i.row() for i in self.panel_table.selectedIndexes()} or {
            self.panel_table.currentRow()
        }
        self._update(panel=[m for i, m in enumerate(self.project.config.panel) if i not in rows])

    def _effort(self, row: int) -> str | None:
        box = self.panel_table.cellWidget(row, 4)
        text = box.currentText().strip()[:20]
        return None if text in ("", box.itemText(0)) else text

    def _apply_panel(self) -> None:
        if self.project is None:
            return
        panel = []
        for row, model in enumerate(self.project.config.panel):
            panel.append(
                model.model_copy(
                    update={
                        **self.panel_table.cellWidget(row, 1).changes(),
                        "max_tokens": self.panel_table.cellWidget(row, 2).value(),
                        "thinking": self.panel_table.cellWidget(row, 3).currentData(),
                        "reasoning_effort": self._effort(row),
                    }
                )
            )
        self._update(panel=panel, refill=False)

    def _update(self, refill: bool = True, **changes: object) -> None:
        assert self.project is not None
        try:
            self.project.update(**changes)
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        self.error.clear()
        self.changed.emit()
        if refill:
            self.refresh()
