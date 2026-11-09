"""Einrichtungs-Assistent für ein neues Projekt: Ordner, Daten, Codebuch, Modell.

Bis zum Fertigstellen liest er nur Dateien; erst dann legt er das Projekt an und speichert
einen API-Key im Schlüsselbund. Alles lässt sich danach in den Tabs ändern.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from llmanno.core import codebook, data_import, providers, units
from llmanno.core.config import (
    GWDG_CHAT_AI,
    KI_CONNECT_NRW,
    OLLAMA_LOCAL,
    Code,
    CodebookConfig,
    ColumnMapping,
    ModelConfig,
    ProviderConfig,
    UnitSettings,
)
from llmanno.core.lock import storage_warning
from llmanno.core.project import Project
from llmanno.core.providers.base import ModelInfo, Provider
from llmanno.core.providers.openai_compat import OpenAICompatProvider
from llmanno.core.store import sha256_text
from llmanno.gui.common import (
    Task,
    checked_entries,
    error_label,
    error_text,
    fill_checklist,
    fill_combo,
    hint_label,
    optional_columns,
)

PREVIEW_ROWS = 5
CONNECT_TIMEOUT_S = 30  # Verbindungstest: nicht minutenlang hängen (und das Schließen blockieren)
_PROVIDERS: tuple[ProviderConfig | None, ...] = (OLLAMA_LOCAL, GWDG_CHAT_AI, KI_CONNECT_NRW, None)

ProviderFactory = Callable[[ProviderConfig, str | None], Provider]


def connect(config: ProviderConfig, api_key: str | None) -> Provider:
    """Wie make_provider, aber mit dem eingegebenen Key: Der kommt erst beim Fertigstellen in
    den Schlüsselbund."""
    if not api_key or config.auth == "none":  # ohne Anmeldung zählt kein Key (etwa Ollama)
        return providers.make_provider(config)
    return OpenAICompatProvider(
        config.base_url, api_key=api_key, auth=config.auth, timeout=config.timeout_s
    )


def _with_hint(widget: QWidget, hint: str) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget)
    layout.addWidget(hint_label(hint))
    return box


class SetupWelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(self.tr("Willkommen"))
        text = QLabel(
            self.tr("Das Programm kodiert Texteinheiten, etwa Gesprächszüge, mit einem Codebuch "
                    "durch Sprachmodelle (LLMs) und prüft das Ergebnis an menschlichen "
                    "Kodierungen. Der Assistent richtet ein neues Projekt ein: Projektordner, "
                    "Daten, Codebuch und Modell. Alles lässt sich danach in den Tabs ändern. "
                    "Ein fertiges Beispiel zeigt „Datei → Demo öffnen“.")
        )  # fmt: skip
        text.setWordWrap(True)
        QVBoxLayout(self).addWidget(text)


class SetupProjectPage(QWizardPage):
    def __init__(self, folder: Path | None) -> None:
        super().__init__()
        self.setTitle(self.tr("Projekt"))
        self.setSubTitle(self.tr("Name, Ordner, Sprache und Kodiermodus."))
        self._folder_ok = False
        self.name_edit = QLineEdit()
        self.folder_edit = QLineEdit(str(folder) if folder else "")
        choose = QPushButton(self.tr("Ordner wählen…"))
        choose.clicked.connect(self._choose_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(choose)
        self.folder_hint = hint_label()
        self.folder_error = error_label()
        self.storage_label = error_label()

        self.language_combo = QComboBox()
        fill_combo(self.language_combo, [(self.tr("Deutsch"), "de"), (self.tr("Englisch"), "en")])
        self.single_radio = QRadioButton(self.tr("Ein Code je Einheit (Single-Label)"))
        self.multi_radio = QRadioButton(self.tr("Mehrere Codes je Einheit (Multi-Label)"))
        self.single_radio.setChecked(True)
        mode = QVBoxLayout()
        mode.addWidget(self.single_radio)
        mode.addWidget(self.multi_radio)
        mode.addWidget(
            hint_label(
                self.tr("Single-Label: Das Modell vergibt genau einen Code, weitere nur als "
                        "Alternativen. Multi-Label: Mehrere Codes sind möglich, wenn sich die "
                        "Kategorien nicht ausschließen.")
            )
        )  # fmt: skip

        form = QFormLayout(self)
        form.addRow(self.tr("Name"), self.name_edit)
        form.addRow(self.tr("Projektordner"), folder_row)
        form.addRow("", self.folder_hint)
        form.addRow("", self.folder_error)
        form.addRow("", self.storage_label)
        form.addRow(
            self.tr("Sprache"),
            _with_hint(
                self.language_combo,
                self.tr("Sprache der Transkripte und der Anweisungen (Prompt) an das Modell."),
            ),
        )
        form.addRow(self.tr("Kodiermodus"), mode)

        self.name_edit.textChanged.connect(self.completeChanged)
        self.folder_edit.textChanged.connect(self._check)
        self._check()

    def folder(self) -> Path:
        return Path(self.folder_edit.text().strip())

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Leeren Ordner für das Projekt wählen"), self.folder_edit.text()
        )
        if folder:
            self.folder_edit.setText(str(Path(folder)))
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(folder).name)

    def _check(self) -> None:
        text = self.folder_edit.text().strip()
        path = Path(text)
        error = hint = warning = ""
        if text and not path.is_absolute():
            error = self.tr("Vollständigen Pfad angeben, etwa C:\\Projekte\\Studie.")
        elif text:
            try:
                if path.is_file():
                    error = self.tr("Das ist eine Datei, kein Ordner.")
                elif path.is_dir() and any(path.iterdir()):
                    error = self.tr("Der Ordner ist nicht leer. Einen leeren Ordner wählen "
                                    "oder einen neuen Namen anhängen; der Ordner wird dann "
                                    "angelegt.")  # fmt: skip
                elif not path.exists():
                    hint = self.tr("Der Ordner wird beim Fertigstellen angelegt.")
                warning = storage_warning(path) or ""
            except OSError as exc:
                error = error_text(exc)
        self._folder_ok = bool(text) and not error
        self.folder_hint.setText(hint)
        self.folder_error.setText(error)
        self.storage_label.setText(warning)
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802 (Qt)
        return self._folder_ok and bool(self.name_edit.text().strip())

    def validatePage(self) -> bool:  # noqa: N802 (Qt)
        self._check()  # der Ordner kann sich seit der Eingabe gefüllt haben
        return self.isComplete()


class SetupDataPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(self.tr("Daten"))
        self.setSubTitle(self.tr("Tabelle mit einer Zeile je Texteinheit wählen und Spalten "
                                 "zuordnen. Importiert wird beim Fertigstellen."))  # fmt: skip
        self.path: Path | None = None
        self._sheet: data_import.Sheet | None = None
        self.report: data_import.ImportReport | None = None

        choose = QPushButton(self.tr("Datei wählen (XLSX, CSV)…"))
        choose.clicked.connect(self._choose_file)
        self.file_label = QLabel()
        self.sheet_combo = QComboBox()
        self.sheet_combo.currentIndexChanged.connect(self._load_sheet)
        top = QHBoxLayout()
        top.addWidget(choose)
        top.addWidget(self.file_label, 1)
        top.addWidget(QLabel(self.tr("Blatt")))
        top.addWidget(self.sheet_combo)

        self.preview = QTableWidget()
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview.setMaximumHeight(160)

        self.text_combo, self.id_combo, self.group_combo = QComboBox(), QComboBox(), QComboBox()
        self.order_combo, self.speaker_combo = QComboBox(), QComboBox()
        self.gold_list = QListWidget()
        self.gold_list.setMaximumHeight(90)
        self.unit_combo = QComboBox()
        fill_combo(
            self.unit_combo,
            [
                (self.tr("Zeile allein"), "isolated"),
                (self.tr("Zeile mit Nachbarzeilen (Kontext)"), "context"),
                (self.tr("Ganze Gruppe"), "holistic"),
            ],
            UnitSettings().mode,
        )
        defaults = UnitSettings()
        self.unit_box = _with_hint(
            self.unit_combo,
            self.tr("Zeile allein: jede Zeile für sich; günstig und gut vergleichbar, Bezüge "
                    "wie „ja, genau“ bleiben aber unklar. Mit Nachbarzeilen: Das Modell liest "
                    "{0} Zeilen davor und {1} danach mit, kodiert aber nur die Zeile; mehr Token "
                    "je Aufruf. Ganze Gruppe: ein Code für die ganze Gruppe, etwa je Transkript. "
                    "Später im Tab Prompt & Einheiten änderbar.")
            .format(defaults.n_before, defaults.n_after),
        )  # fmt: skip

        form = QFormLayout()
        form.addRow(
            self.tr("Text"),
            _with_hint(self.text_combo, self.tr("Der Text, den das Modell kodiert (Pflicht).")),
        )
        form.addRow(
            self.tr("ID"),
            _with_hint(
                self.id_combo,
                self.tr("Eindeutige Kennung je Zeile (empfohlen). Ohne ID heißen die Einheiten "
                        "nach ihrer Zeilennummer; die ändert sich beim Sortieren.")
            ),
        )  # fmt: skip
        form.addRow(
            self.tr("Gruppe"),
            _with_hint(
                self.group_combo,
                self.tr("Zusammengehörige Zeilen, etwa ein Transkript. Nötig, damit das Modell "
                        "Nachbarzeilen mitlesen kann (Kontext).")
            ),
        )  # fmt: skip
        form.addRow(
            self.tr("Reihenfolge"),
            _with_hint(
                self.order_combo,
                self.tr("Zahl für die Abfolge innerhalb der Gruppe; ohne sie gilt die "
                        "Zeilenfolge der Datei.")
            ),
        )  # fmt: skip
        form.addRow(
            self.tr("Sprecher:in"),
            _with_hint(self.speaker_combo, self.tr("Wer spricht; steht vor dem Text im Prompt.")),
        )
        form.addRow(
            self.tr("Gold (optional)"),
            _with_hint(
                self.gold_list,
                self.tr("Spalten mit menschlichen Kodierungen (Referenzkodierung), eine je "
                        "Person. Daran wird das Modell später gemessen.")
            ),
        )  # fmt: skip
        self.unit_label = QLabel(self.tr("Was soll das Modell auf einmal sehen?"))
        form.addRow(self.unit_label, self.unit_box)
        self.error = error_label()

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.addLayout(top)
        body_layout.addWidget(self.preview)
        body_layout.addLayout(form)
        body_layout.addWidget(self.error)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        QVBoxLayout(self).addWidget(scroll)

        for combo in (self.text_combo, self.group_combo):
            combo.currentIndexChanged.connect(self._changed)
        self._changed()

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Daten wählen"), "", self.tr("Daten (*.xlsx *.xlsm *.csv *.tsv)")
        )
        if path:
            self.open_file(Path(path))

    def open_file(self, path: Path) -> None:
        self.error.clear()
        try:
            names = data_import.sheet_names(path)
        except Exception as exc:  # openpyxl wirft je nach Defekt sehr verschiedene Fehler
            self.error.setText(self.tr("Datei nicht lesbar: {0}").format(error_text(exc)))
            return
        self.path = path
        self.file_label.setText(path.name)
        fill_combo(self.sheet_combo, ((n, n) for n in names))
        self._load_sheet()

    def _load_sheet(self) -> None:
        if self.path is None:
            return
        self.error.clear()
        try:
            self._sheet = data_import.read_sheet(self.path, self.sheet_combo.currentData())
        except Exception as exc:
            self._sheet = None
            self.error.setText(self.tr("Blatt nicht lesbar: {0}").format(error_text(exc)))
            self._changed()
            return
        header = self._sheet.header
        rows = [values for _, values in self._sheet.rows[:PREVIEW_ROWS]]
        self.preview.clear()
        self.preview.setColumnCount(len(header))
        self.preview.setHorizontalHeaderLabels(header)
        self.preview.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                self.preview.setItem(r, c, QTableWidgetItem("" if value is None else str(value)))
        self.preview.resizeColumnsToContents()
        # Raten wie im Tab Daten
        lower = {c.lower(): c for c in header}
        guess_id = next((lower[n] for n in ("id", "nr", "nummer") if n in lower), None)
        guess_text = next((c for c in header if "text" in c.lower()), None)
        fill_combo(self.text_combo, optional_columns(header), guess_text)
        fill_combo(self.id_combo, optional_columns(header), guess_id)
        for combo in (self.group_combo, self.order_combo, self.speaker_combo):
            fill_combo(combo, optional_columns(header))
        fill_checklist(self.gold_list, header, [])
        self._changed()

    def _changed(self) -> None:
        grouped = self.group_combo.currentData() is not None
        self.unit_label.setVisible(grouped)
        self.unit_box.setVisible(grouped)
        self.completeChanged.emit()

    def mapping(self) -> ColumnMapping:
        return ColumnMapping(
            id=self.id_combo.currentData(),
            text=[self.text_combo.currentData()],
            group=self.group_combo.currentData(),
            order=self.order_combo.currentData(),
            speaker=self.speaker_combo.currentData(),
            gold=checked_entries(self.gold_list),
        )

    def unit_settings(self) -> UnitSettings:
        grouped = self.group_combo.currentData() is not None
        return UnitSettings(mode=self.unit_combo.currentData() if grouped else "isolated")

    def isComplete(self) -> bool:  # noqa: N802 (Qt)
        return self._sheet is not None and self.text_combo.currentData() is not None

    def validatePage(self) -> bool:  # noqa: N802 (Qt)
        """Probeimport ohne Schreiben: Fehler hier statt erst beim Fertigstellen."""
        if not self.isComplete():
            return False
        self.error.clear()
        try:
            report = data_import.import_units(self._sheet, self.mapping())
            if not report.units:
                first = next((i.message for i in report.issues if i.severity == "error"), "")
                raise ValueError(
                    self.tr("Keine gültige Einheit gefunden. Text- und ID-Spalte prüfen. {0}")
                    .format(first)
                )  # fmt: skip
            units.build_items(report.units, self.unit_settings())
        except ValueError as exc:
            self.error.setText(error_text(exc))
            return False
        self.report = report
        return True


class SetupCodebookPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(self.tr("Codebuch"))
        self.setSubTitle(self.tr("Textdatei mit Definitionen, Regeln und Ankerbeispielen. Sie "
                                 "geht unverändert an das Modell."))  # fmt: skip
        self.path: Path | None = None
        self.config: CodebookConfig | None = None
        self._loaded: codebook.CodebookText | None = None
        self._codes: list[Code] = []

        choose = QPushButton(self.tr("Codebuch wählen (.txt, .md)…"))
        choose.clicked.connect(self._choose_file)
        self.file_label = QLabel()
        top = QHBoxLayout()
        top.addWidget(choose)
        top.addWidget(self.file_label, 1)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([self.tr("Kürzel"), self.tr("Label")])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.status = hint_label()
        self.abstain_check = QCheckBox(self.tr("Restkategorie „kein Code passt“ anbieten"))
        self.abstain_check.setChecked(CodebookConfig.model_fields["abstain"].default)
        self.abstain_check.toggled.connect(self._update)
        self.issues = error_label()
        self.error = error_label()

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(QLabel(self.tr("Erkannte Codes (Code-Liste) zur Kontrolle")))
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.abstain_check)
        layout.addWidget(
            hint_label(
                self.tr("Erlaubt dem Modell zu sagen, dass kein Code passt, statt einen zu "
                        "erzwingen. Code-Liste und Restkategorie lassen sich im Tab Codebuch "
                        "ändern.")
            )
        )  # fmt: skip
        layout.addWidget(self.issues)
        layout.addWidget(self.error)

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Codebuch wählen"),
            "",
            self.tr("Textdateien (*.txt *.md);;Alle Dateien (*)"),
        )
        if path:
            self.open_file(Path(path))

    def open_file(self, path: Path) -> None:
        self.error.clear()
        try:
            self._loaded = codebook.load_codebook(path)
        except (OSError, ValueError) as exc:
            self._loaded, self.path, self._codes = None, None, []
            self.error.setText(error_text(exc))
        else:
            self.path = path
            found = codebook.suggest_auto(self._loaded.text)
            self._codes = [Code(id=s.id, label=s.label) for s in found]
        self.file_label.setText(path.name)
        self.table.setRowCount(len(self._codes))
        for row, code in enumerate(self._codes):
            self.table.setItem(row, 0, QTableWidgetItem(code.id))
            self.table.setItem(row, 1, QTableWidgetItem(code.label))
        if self._loaded is None:
            self.status.clear()
        elif self._codes:
            self.status.setText(
                self.tr("{0} Codes erkannt. Bitte prüfen; die Liste legt fest, welche Codes das "
                        "Modell vergeben darf.").format(len(self._codes))
            )  # fmt: skip
        else:
            self.status.setText(
                self.tr("Keine Codes erkannt. Im Tab Codebuch ein anderes Muster wählen oder "
                        "die Codes von Hand eintragen; vorher lässt sich nicht kodieren.")
            )  # fmt: skip
        self._update()

    def _update(self) -> None:
        self.config = None
        self.issues.clear()
        if self._loaded is not None:
            try:
                self.config = CodebookConfig(
                    source_name=self.path.name,
                    sha256=sha256_text(self._loaded.text),
                    encoding=self._loaded.encoding,
                    codes=self._codes,
                    abstain=self.abstain_check.isChecked(),
                )
            except ValueError as exc:  # etwa ein Code „KEIN_CODE“ neben der Restkategorie
                self.error.setText(error_text(exc))
            else:
                self.error.clear()
                issues = codebook.code_list_issues(self.config, self._loaded.text)
                self.issues.setText("\n".join(issues))
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802 (Qt)
        return self.config is not None


class SetupModelPage(QWizardPage):
    def __init__(self, factory: ProviderFactory) -> None:
        super().__init__()
        self.setTitle(self.tr("Modell"))
        self.setSubTitle(self.tr("Wo dürfen die Texte verarbeitet werden?"))
        self._factory = factory
        self._task: Task | None = None
        self._infos: dict[str, ModelInfo] = {}
        self._stored_key = False

        self.choice = QButtonGroup(self)
        layout = QVBoxLayout(self)
        labels = (
            self.tr("Ollama auf diesem Rechner"),
            self.tr("GWDG/KISSKI (akademische API)"),
            self.tr("KI:connect.NRW (akademische API)"),
            self.tr("Später im Tab Modelle einrichten"),
        )
        for index, text in enumerate(labels):
            radio = QRadioButton(text)
            self.choice.addButton(radio, index)
            layout.addWidget(radio)
        self.choice.button(len(labels) - 1).setChecked(True)
        self.choice_hint = hint_label()
        layout.addWidget(self.choice_hint)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText(self.tr("API-Key eingeben"))
        self.key_hint = hint_label()
        self.key_box = QWidget()
        key_form = QFormLayout(self.key_box)
        key_form.setContentsMargins(0, 0, 0, 0)
        key_form.addRow(self.tr("API-Key"), self.key_edit)
        key_form.addRow("", self.key_hint)
        layout.addWidget(self.key_box)

        self.test_button = QPushButton(self.tr("Verbindung testen"))
        self.test_button.clicked.connect(self.test_connection)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(260)
        self.notes = hint_label()
        self.error = error_label()
        self.model_box = QWidget()
        model_form = QFormLayout(self.model_box)
        model_form.setContentsMargins(0, 0, 0, 0)
        model_form.addRow("", self.test_button)
        model_form.addRow(self.tr("Modell"), self.model_combo)
        model_form.addRow("", self.notes)
        model_form.addRow("", self.error)
        layout.addWidget(self.model_box)
        layout.addStretch()

        self.choice.idToggled.connect(lambda _id, on: on and self._choice_changed())
        self.key_edit.textChanged.connect(self.completeChanged)
        self.model_combo.currentTextChanged.connect(self.completeChanged)
        self._choice_changed()

    def preset(self) -> ProviderConfig | None:
        return _PROVIDERS[self.choice.checkedId()]

    @property
    def busy(self) -> bool:
        return self._task is not None and self._task.running

    def wait(self) -> None:
        if self._task is not None:
            self._task.wait()

    def _choice_changed(self) -> None:
        preset = self.preset()
        needs_key = preset is not None and preset.auth != "none"
        self.key_box.setVisible(needs_key)
        self.model_box.setVisible(preset is not None)
        self._infos = {}
        self.model_combo.clear()
        self.key_edit.clear()  # ein Key gilt nur für den Anbieter, für den er getippt wurde
        self.notes.clear()
        self.error.clear()
        self._stored_key = False
        if needs_key:
            try:
                self._stored_key = bool(providers.get_api_key(preset))
            except Exception:  # kein Schlüsselbund-Backend: dann eben eingeben
                self._stored_key = False
        if preset is None:
            hint = self.tr("Das Projekt wird ohne Modell angelegt; im Tab Modelle lässt sich "
                           "später eins einrichten. Bis dahin verlässt kein Text den "
                           "Rechner.")  # fmt: skip
        elif preset.privacy == "local":
            hint = self.tr("Die Texte bleiben auf diesem Rechner. Ollama muss installiert sein und "
                           "laufen (ollama.com). Bei etwa 4 GB Grafikspeicher passen Modelle bis "
                           "etwa 4 Mrd. Parameter.")  # fmt: skip
        else:
            hint = self.tr("Die Texte verlassen diesen Rechner und gehen an den Anbieter. Vorher "
                           "prüfen, ob das für die Daten zulässig ist. Vor jedem Lauf fragt das "
                           "Programm nach Zustimmung.")  # fmt: skip
        self.choice_hint.setText(hint)
        key_hint = self.tr("Den API-Key im Portal des Anbieters erzeugen, Zugang über die eigene "
                           "Hochschule. Er wird beim Fertigstellen im Schlüsselbund des "
                           "Betriebssystems gespeichert, nie im Projekt.")  # fmt: skip
        if self._stored_key:
            key_hint += " " + self.tr("Für diese Adresse ist schon ein Key hinterlegt; ein neu "
                                      "eingegebener ersetzt ihn.")  # fmt: skip
        self.key_hint.setText(key_hint)
        self.completeChanged.emit()

    def test_connection(self) -> None:
        preset = self.preset()
        if preset is None or self.busy:
            return
        config = preset.model_copy(update={"timeout_s": CONNECT_TIMEOUT_S})
        key = self.key_edit.text().strip() if preset.auth != "none" else ""
        if preset.auth != "none" and not key and not self._stored_key:
            self.error.setText(self.tr("Erst den API-Key eingeben."))
            return
        factory = self._factory

        def work(report: object) -> list[ModelInfo]:
            provider = factory(config, key)
            try:
                return provider.list_models()
            finally:
                provider.close()

        self.error.clear()
        self.notes.setText(self.tr("Läuft …"))
        self.test_button.setEnabled(False)
        self._task = Task(self, work)
        self._task.done.connect(lambda models: self._loaded(preset, models))
        self._task.failed.connect(lambda exc: self._failed(preset, exc))
        self._task.finished.connect(lambda: self.test_button.setEnabled(True))
        self._task.start()

    def _loaded(self, preset: ProviderConfig, models: list[ModelInfo]) -> None:
        if self.preset() is not preset:  # inzwischen anderer Anbieter gewählt
            return
        self._infos = {m.name: m for m in models}
        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems(list(self._infos))
        if current in self._infos:
            self.model_combo.setCurrentText(current)
        self.notes.setText(self.tr("Verbindung steht, {0} Modelle gefunden.").format(len(models)))

    def _failed(self, preset: ProviderConfig, exc: BaseException) -> None:
        if self.preset() is preset:
            self.notes.clear()
            self.error.setText(error_text(exc))

    def model_config(self) -> ModelConfig | None:
        preset = self.preset()
        if preset is None:
            return None
        name = self.model_combo.currentText().strip()
        info = self._infos.get(name)
        # wie im Tab Modelle: Ollama-Modelle, die denken können, ohne Thinking
        thinking = (
            False if preset.kind == "ollama" and info and "thinking" in info.capabilities else None
        )
        thinker = providers.is_thinking_model(name, info.capabilities if info else ())
        tokens = {"max_tokens": providers.THINKING_TOKENS} if thinker else {}
        return ModelConfig(provider=preset.key, model=name, thinking=thinking, **tokens)

    def small_model(self) -> bool:
        """Unter 10 Mrd. Parametern ist der Evidenz-Repair aus, wie im Tab Modelle."""
        size = providers.parameter_billions(self._infos.get(self.model_combo.currentText().strip()))
        return size is not None and size < providers.SMALL_MODEL_BILLIONS

    def isComplete(self) -> bool:  # noqa: N802 (Qt)
        preset = self.preset()
        if preset is None:
            return True
        has_key = preset.auth == "none" or self._stored_key or bool(self.key_edit.text().strip())
        return has_key and bool(self.model_combo.currentText().strip())


class SetupFinishPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(self.tr("Fertig"))
        self.setSubTitle(self.tr("„Fertigstellen“ legt das Projekt an."))
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.next_step = QLabel()
        self.next_step.setWordWrap(True)
        self.error = error_label()
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(QLabel(self.tr("Nächster Schritt")))
        layout.addWidget(self.next_step)
        layout.addWidget(self.error)
        layout.addStretch()


class SetupWizard(QWizard):
    """Nach `accept()` liegt das angelegte, geöffnete Projekt in `project`."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        provider_factory: ProviderFactory = connect,
        folder: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.project: Project | None = None
        self.setWindowTitle(self.tr("Neues Projekt einrichten"))
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage)
        self.setMinimumSize(760, 620)
        self.welcome_page = SetupWelcomePage()
        self.project_page = SetupProjectPage(folder)
        self.data_page = SetupDataPage()
        self.codebook_page = SetupCodebookPage()
        self.model_page = SetupModelPage(provider_factory)
        self.finish_page = SetupFinishPage()
        for page in (
            self.welcome_page,
            self.project_page,
            self.data_page,
            self.codebook_page,
            self.model_page,
            self.finish_page,
        ):
            self.addPage(page)

    def initializePage(self, id: int) -> None:  # noqa: A002, N802 (Qt)
        super().initializePage(id)
        if self.page(id) is self.finish_page:
            self._summarize()

    def validateCurrentPage(self) -> bool:  # noqa: N802 (Qt)
        if not super().validateCurrentPage():
            return False
        return self.currentPage() is not self.finish_page or self._create()

    def done(self, result: int) -> None:
        self.model_page.wait()  # ein laufender QThread darf nicht mit dem Fenster zerstört werden
        super().done(result)

    def _summarize(self) -> None:
        p, d, c, m = self.project_page, self.data_page, self.codebook_page, self.model_page
        mode = (self.tr("Mehrere Codes je Einheit (Multi-Label)") if p.multi_radio.isChecked()
                else self.tr("Ein Code je Einheit (Single-Label)"))  # fmt: skip
        report = d.report
        gold = ", ".join(d.mapping().gold) or self.tr("keine")
        lines = [
            self.tr("Projekt: {0} in {1}").format(p.name_edit.text().strip(), p.folder()),
            self.tr("Sprache: {0} · {1}").format(p.language_combo.currentText(), mode),
            self.tr("Daten: {0}, Blatt „{1}“: {2} Einheiten, {3} Zeilen übersprungen").format(
                d.path.name if d.path else "–",
                d.sheet_combo.currentText(),
                len(report.units) if report else 0,
                report.n_skipped if report else 0,
            ),
            self.tr("Gold: {0}").format(gold),
            self.tr("Was das Modell auf einmal sieht: {0}").format(
                d.unit_combo.currentText()
                if d.group_combo.currentData()
                else self.tr("Zeile allein")
            ),
            self.tr("Codebuch: {0}, {1} Codes, Restkategorie {2}").format(
                c.path.name if c.path else "–",
                len(c.config.codes) if c.config else 0,
                self.tr("an") if c.abstain_check.isChecked() else self.tr("aus"),
            ),
        ]
        model, preset = m.model_config(), m.preset()
        if model is None or preset is None:
            lines.append(self.tr("Modell: später im Tab Modelle"))
        else:
            lines.append(
                self.tr("Modell: {0} bei {1}, max. {2} Token je Antwort").format(
                    model.model, preset.name, model.max_tokens
                )
            )
            if m.small_model():
                lines.append(self.tr("Kleines Modell: Evidenz-Repair aus (im Tab Prompt & "
                                     "Einheiten einschaltbar)."))  # fmt: skip
        self.finish_page.summary.setText("\n".join(lines))
        steps = []
        if c.config is not None and not c.config.codes:
            steps.append(self.tr("Im Tab Codebuch die Code-Liste eintragen."))
        if model is None:
            steps.append(self.tr("Im Tab Modelle ein Modell ins Panel übernehmen."))
        steps.append(
            self.tr("Im Tab Prompt & Einheiten die Vorschau prüfen, Namen ersetzen (Datenschutz), "
                    "dann im Tab Lauf den Pilot starten. Mit Gold vorher im Tab Splits & Freeze "
                    "aufteilen.")
        )  # fmt: skip
        self.finish_page.next_step.setText(" ".join(steps))
        self.finish_page.error.clear()

    def _create(self) -> bool:
        p, d, c, m = self.project_page, self.data_page, self.codebook_page, self.model_page
        error = self.finish_page.error
        error.clear()
        folder = p.folder()
        try:
            project = Project.create(folder, p.name_edit.text().strip())
        except Exception as exc:
            error.setText(self.tr("Projekt nicht angelegt: {0}").format(error_text(exc)))
            return False
        try:
            project.update(
                prompt_language=p.language_combo.currentData(),
                mode="multi" if p.multi_radio.isChecked() else "single",
            )
            project.import_data(d.path, d.sheet_combo.currentData(), d.mapping())
            project.load_codebook(c.path)
            config = project.config
            coding = config.coding.model_copy(update={"units": d.unit_settings()})
            if m.small_model():
                coding = coding.model_copy(update={"evidence_repair": False})
            changes: dict[str, object] = {
                "codebook": config.codebook.model_copy(
                    update={"codes": c.config.codes, "abstain": c.config.abstain}
                ),
                "coding": coding,
            }
            preset, model = m.preset(), m.model_config()
            if preset is not None and model is not None:
                if preset.key not in {x.key for x in config.providers}:
                    changes["providers"] = [*config.providers, preset]
                changes["panel"] = [model]
            project.update(**changes)
            project.save()
        except Exception as exc:
            project.close()  # halb eingerichtet: schließen, nichts löschen
            error.setText(
                self.tr("Das Projekt in {0} ist angelegt, aber nicht fertig eingerichtet: {1}\n"
                        "Der Ordner bleibt so; mit Datei → Öffnen lässt es sich in den Tabs "
                        "weiter einrichten. Für einen neuen Versuch einen anderen leeren Ordner "
                        "wählen.").format(folder, error_text(exc))
            )  # fmt: skip
            return False
        key = m.key_edit.text().strip()
        if preset is not None and preset.auth != "none" and key:
            try:
                providers.set_api_key(preset, key)
                project.store.log("api_key_set", provider=preset.key, base_url=preset.base_url)
            except Exception as exc:  # Projekt steht; nur der Key fehlt noch
                QMessageBox.warning(
                    self, self.tr("API-Key nicht gespeichert"),
                    self.tr("Das Projekt ist angelegt, aber der API-Key ließ sich nicht im "
                            "Schlüsselbund speichern: {0}\nIm Tab Modelle erneut eingeben."
                            ).format(error_text(exc)),
                )  # fmt: skip
        self.project = project
        return True


def run_setup_wizard(parent: QWidget | None) -> Project | None:
    """Assistent modal zeigen; das angelegte, geöffnete Projekt oder None (abgebrochen)."""
    wizard = SetupWizard(parent)
    wizard.exec()
    return wizard.project
