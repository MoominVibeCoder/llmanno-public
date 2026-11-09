# LLM-Annotation (Arbeitstitel)

*English: [README.en.md](README.en.md)*

Desktop-Programm für deduktive LLM-Kodierung: XLSX-Daten, Codebuch als Freitext, Modell-Panel (Ollama lokal oder OpenAI-kompatible APIs), Top-k-Codes mit Konfidenz, Begründung und geprüften Signalwörtern, Validierung gegen menschliche Annotationen, Export nach REFI-QDA und Statistiksoftware.

Status: **Phasen 1 bis 4 umgesetzt, Phase 5 (Publikationsreife) in Arbeit.** Das Programm kann:
- XLSX- und CSV-Daten importieren, auch mehrere Dateien oder Blätter zusammengeführt
- mit einem Modell oder einem Panel kodieren
- gegen menschliche Kodierungen mit allen üblichen Kennwerten auswerten
- einen Dev/Test-Split mit Freeze und Präregistrierung anlegen
- eine Review-Queue mit blinden Reviews als Gold führen
- nach Subgruppen auswerten und die Prävalenz bias-korrigiert per PPI schätzen
- nach XLSX, SPSS, Stata und R, `dsl` sowie REFI-QDA (.qdpx/.qdc) exportieren
- mehrere Codesysteme je Projekt kodieren (je eigenes Codebuch, eigene Aufrufe und eigene Validierung) und Namen vor dem Senden durch Pseudonyme ersetzen
- einen Methoden-Report (Word oder HTML) und ein Replikationspaket mit Headless-Rerun erzeugen

Abnahmeläufe über KI:connect.NRW sind dokumentiert in [docs/PLAN.md](docs/PLAN.md), Abschnitt 10: gpt-oss-120b einzeln vollständig, das Panel gpt-oss-120b + qwen3.8-27b abgebrochen, weil qwen dort zu langsam war. Die Anleitung mit methodischen Empfehlungen steht in [docs/ANLEITUNG.md](docs/ANLEITUNG.md), der vollständige Plan in [docs/PLAN.md](docs/PLAN.md).

## Erste Schritte mit dem Beispielprojekt

Am schnellsten geht es mit **Demo öffnen** (Projekt-Tab oder Menü Datei): Das Programm legt das Beispielprojekt an und füllt es mit simulierten Antworten (kein LLM, keine eigenen Daten nötig), sodass sich alle Tabs ansehen lassen.

Dasselbe Beispiel zum Selbermachen liegt in `src/llmanno/demo/` (in der Exe im Ordner `Beispiel`): 200 synthetische Gesprächszüge aus Mathestunden (`daten.xlsx`, Blatt „Turns“) und ein passendes Codebuch (`codebuch.md`). Neu erzeugen mit `uv run python examples/make_example.py`.

1. **Projekt:** Neues Projekt in einem leeren Ordner anlegen.
2. **Daten:** `daten.xlsx` wählen. ID und Text werden erraten. Gruppe = Transkript, Reihenfolge = Nr, Sprecher:in = Sprecher, Gold = Coder A und Coder B. Dann „Importieren“ und den Prüfbericht ansehen.
3. **Codebuch:** `codebuch.md` laden. K1 bis K4 werden vorgeschlagen; die Liste prüfen, sie ist verbindlich.
4. **Modelle:** Beim Anbieter „Ollama (lokal)“ auf „Verbindung testen“ klicken, ein Modell wählen und „Ins Panel übernehmen“.
5. **Prompt & Einheiten:** „Zeile mit Kontextfenster“ wählen und den gerenderten Prompt in der Vorschau prüfen.
6. **Lauf:** Erst den Pilot starten, er rechnet die Dauer hoch. Dann den Vollauf; „Anhalten“ verliert nichts.
7. **Ergebnisse:** Tabelle mit Filtern, Signalwörter im Text hervorgehoben.
8. **Splits & Freeze** (vor dem Lauf, für eine saubere Validierung): Split erzeugen, auf Dev schärfen („nur Dev-Einheiten“ im Tab Lauf), einfrieren, Präregistrierung exportieren, dann den Vollauf starten.
9. **Review:** Queue erzeugen und unsichere Fälle plus eine Zufallsstichprobe blind kodieren. Nur blinde Reviews zählen als Gold.
10. **Evaluation:** Teilmenge wählen (Test erst nach dem Freeze), optional Subgruppen, Kennwerte berechnen, Methoden-Report speichern.
11. **Export:** XLSX, REFI-QDA für MAXQDA, ATLAS.ti und QualCoder, Replikationspaket mit oder ohne Rohtexte.

Neu rechnen aus einem Replikationspaket:

```bash
uv run llmanno-rerun replikation.zip --out rerun
```

Ollama muss laufen (`ollama serve`). Externe Anbieter wie GWDG/KISSKI oder KI:connect.NRW brauchen einen API-Key, der im Windows-Schlüsselbund gespeichert wird; vor jedem Lauf mit externem Anbieter fragt das Programm nach Zustimmung.

## Installation

Die Windows-Fassung braucht kein Python: ZIP entpacken und `llmanno.exe` starten (Details in [docs/ANLEITUNG.md](docs/ANLEITUNG.md), Abschnitt „Installation und Start“). Aus dem Quelltext wie unter „Entwicklung“.

## Entwicklung

Voraussetzung: [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
uv run llmanno
```

```bash
uv run pytest
```

```bash
uv run ruff check .
```

## Windows-Build

```bash
uv run pyinstaller packaging/llmanno.spec --noconfirm
```

Ergebnis ist der Ordner `dist/llmanno/` (rund 260 MB) mit `llmanno.exe` (Oberfläche) und `llmanno-rerun.exe` (Konsole). Er läuft ohne Python-Installation; zum Weitergeben den ganzen Ordner zippen. Die Programme sind nicht signiert, Windows SmartScreen fragt deshalb beim ersten Start nach.

## Übersetzungen

Quellsprache der Oberfläche ist Deutsch. Für eine weitere Sprache werden die Texte extrahiert, in Qt Linguist übersetzt und als `.qm` nach `src/llmanno/i18n/` kompiliert:

```bash
uv run pyside6-lupdate src/llmanno/gui/*.py -source-language de -target-language en -ts src/llmanno/i18n/llmanno_en.ts
```

```bash
uv run pyside6-lrelease src/llmanno/i18n/llmanno_en.ts -qm src/llmanno/i18n/llmanno_en.qm
```

Englisch liegt bei (`llmanno_en.ts` und `.qm`). Die Sprache wählt das Menü „Sprache“; sie gilt ab dem nächsten Start (QSettings-Schlüssel `ui/language`, Default `de`).

## Stände aus dem Paper

Dieses Repository beginnt mit einem bereinigten Stand ohne die Entwicklungshistorie. Die in den Studien
verwendeten Stände (Commits `9182f86` und `4b7df4f` des Entwicklungs-Repos) liegen als Quellarchive in den
Replikationspaketen: https://doi.org/10.5281/zenodo.23911470

## Lizenz

GNU Affero General Public License, Version 3 oder (nach Wahl) jede spätere Version ([LICENSE](LICENSE)). Die Windows-Fassung bündelt Qt und PySide6 unter der LGPL-3.0 sowie weitere Pakete unter ihren eigenen Lizenzen; die Texte liegen im Build unter `Lizenzen/` und in den `*.dist-info`-Ordnern ([packaging/licenses/HINWEISE.txt](packaging/licenses/HINWEISE.txt)).

Neue Texte nach dem `lupdate` in Qt Linguist übersetzen (`uv run pyside6-linguist src/llmanno/i18n/llmanno_en.ts`), dann `lrelease`.

Noch deutsch, auch bei englischer Oberfläche: Meldungen und Warnungen aus dem Programmkern, Präregistrierung, Methoden-Report und Exporte. Der Methodenabsatz im Report liegt in beiden Sprachen vor.
