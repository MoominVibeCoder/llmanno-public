# LLM-Annotation: Projektplan

Stand: 2026-10-06 · Entwurf v0.1 nach Interview (5 Runden)
Status: P0 bis P4 umgesetzt, P5 begonnen (Abschnitt 10). Anleitung für Nutzer:innen: [ANLEITUNG.md](ANLEITUNG.md)

---

## 0. Kurzfassung

Ein Desktop-Programm (Python ≥ 3.12, PySide6), das tabellarische Daten (XLSX) deduktiv mit einem Codebuch (Freitext) kodiert. Kodiert wird von einem **Panel aus LLMs**, lokal über Ollama oder über OpenAI-kompatible Chat-APIs (GWDG/KISSKI Chat AI als Voreinstellung, weitere Anbieter frei konfigurierbar).

Pro Einheit liefert das Programm:

- **bis zu 3 Codes** (einstellbar 1 bis 3), jeweils mit **Konfidenz**: empirisch aus wiederholten Läufen (Self-Consistency) und zusätzlich verbalisiert
- eine **kurze Begründung**
- **Signalwörter**, die wörtlich im Text stehen und deshalb **automatisch geprüft** und als Zeichen-Spans verortet werden

Ergebnisse lassen sich mit vorhandenen menschlichen Annotationen vergleichen, mit allen in der empirischen Forschung üblichen Kennwerten samt Bootstrap-Konfidenzintervallen. Exportiert wird nach REFI-QDA (MAXQDA, ATLAS.ti, NVivo, QualCoder), XLSX/CSV, SPSS/Stata/R, dazu ein Methoden-Report und ein Replikationspaket.

### Leitprinzipien

Sie stammen aus der Literatur (Abschnitt 14) und den Regeln deiner Landkarte LLM-Kodierung.

1. **Das Codebuch ist das Instrument.** Es geht verbatim in den Prompt, wird gehasht, versioniert und vor der Testauswertung eingefroren.
2. **Validierung gegen menschliches Gold, pro Code, mit Konfidenzintervallen.** Prompt und Codebuch werden nie am Test-Set geschärft (Baumann et al. 2025, „LLM-Hacking“).
3. **Local-First.** Ollama ist voreingestellt. Ein API-Lauf startet erst nach ausdrücklicher Bestätigung, die protokolliert wird.
4. **Alles ist reproduzierbar.** Jeder Aufruf wird mit Modell-Digest bzw. -Version, Parametern, Seed, Roh-Antwort und Zeitstempel protokolliert.
5. **Unsicherheit wird sichtbar gemacht, nicht versteckt.** Konfidenz kommt aus Wiederholungen und Panel-Dissens. „Kein Code passt“ ist erlaubt.
6. **Belege werden geprüft.** Signalwörter müssen im Zieltext stehen, sonst wird die Antwort markiert.
7. **Eine Begründung ist keine Erklärung.** LLM-Begründungen sind nachträgliche Plausibilisierungen und geben die tatsächlichen Gründe nicht zuverlässig wieder (Turpin et al. 2023). Das Tool weist sie entsprechend aus.

---

## 1. Entscheidungen aus dem Interview

| Thema | Entscheidung | Folge für den Plan |
|---|---|---|
| Kodiermodus | Single- **und** Multi-Label, pro Projekt wählbar | zwei Ausgabeschemata, zwei Aggregationslogiken, zwei Metrik-Sätze |
| Analyseeinheit | Zeile isoliert, Zeile + Kontextfenster, Gruppe holistisch | Einheiten-Builder mit drei Modi, Gruppen- und Reihenfolge-Spalte |
| Nutzerkreis | erst intern, später publizieren | Lizenz und Doku publikationsfähig planen, Installer erst in Phase 5 |
| Konfidenz | Self-Consistency (N Läufe) + verbalisiert | beide werden gespeichert, kalibriert und berichtet. Primär angezeigt wird Self-Consistency. |
| Panel | ab v1 | Panel-Konfiguration, Aggregation, Inter-Modell-α schon in v1 |
| Few-Shot | Ankerbeispiele aus dem Codebuch, Zero-Shot als Option | keine Beispiele aus den Daten, also keine Leakage-Gefahr durch Beispiel-Auswahl. Seit v2 (2026-10-08) optional Beispiele aus Dev, standardmäßig aus; die Beispiel-Einheiten fehlen in allen Auswertungen (`core/examples.py`). |
| Validierung | Dev/Test-Split + Freeze, Review-Queue, bias-korrigierte Schätzung (PPI) | Split-Manager, Instrument-Hash, blinde Review, PPI++ als eigene Implementierung |
| Gold | variabel: 1 bis k Kodierer:innen ± Konsens | Mensch-Mensch-Baseline, Fleiss/Krippendorff, Alt-Test ab 3 Kodierer:innen |
| Export | REFI-QDA (.qdpx/.qdc), XLSX/CSV + SPSS/Stata/R, Report + Replikationspaket | Label-Studio/INCEpTION-Export ist nicht in v1 |
| Datenschutz | lokal als Default, Warnung vor API-Läufen, Keys im OS-Schlüsselbund | Bestätigungsdialog, Datenschutz-Stufe pro Anbieter |
| Codebuch | Freitext, verbatim | Code-Liste per Heuristik vorgeschlagen und per Bestätigungstabelle festgelegt |
| QDA-Segment | Einheit + Signalwort-Spans | eigener Code-Zweig „LLM-Evidenz“ im Export |
| Restkategorie | „kein Code passt“ als zuschaltbare Option | eigene Enum-Ausprägung, wird in den Metriken separat ausgewiesen |
| Datenmenge | unter 2.000 Einheiten | sequenzieller Lauf reicht. Panel × N vervielfacht die Aufrufe aber, siehe Abschnitt 7. |
| Hardware | Laptop mit ca. 4 GB VRAM | lokal nur Modelle bis ca. 4B. Adaptives N, modellweise Reihenfolge, Pilot-ETA. |
| API-Anbieter | GWDG/KISSKI Chat AI + selbst konfigurierbare Anbieter | Anbieter-Registry mit Fähigkeits-Probe |
| Hierarchische Codes | v2 | Datenmodell bekommt jetzt schon ein optionales Feld `parent` |
| UI-Sprache | Deutsch, Englisch vorbereitet | alle Strings über `QCoreApplication.translate`, `.ts`-Dateien |

---

## 2. Arbeitsablauf im Programm

```
 ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌─────────────┐   ┌──────────┐
 │ 1 Projekt│──▶│ 2 Daten   │──▶│3 Codebuch│──▶│ 4 Modelle & │──▶│ 5 Pilot  │
 │ anlegen  │   │ XLSX +    │   │ + Code-  │   │   Prompt    │   │ 20 Einh. │
 └──────────┘   │ Mapping   │   │ Liste    │   │ (Vorschau)  │   │ + ETA    │
                └───────────┘   └──────────┘   └─────────────┘   └────┬─────┘
                                                                      │
   ┌──────────────────────── Dev-Schleife (nur Dev-Split) ◀───────────┘
   │  Codebuch/Prompt anpassen → Dev kodieren → Dev-Kennwerte
   ▼
 ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌─────────────┐   ┌──────────┐
 │ 6 Freeze │──▶│ 7 Vollauf │──▶│ 8 Review │──▶│ 9 Evaluation│──▶│10 Export │
 │ + Präreg.│   │ Test+Rest │   │ (blind)  │   │ einmalig    │   │ + Report │
 └──────────┘   └───────────┘   └──────────┘   │ auf Test    │   └──────────┘
                                               └─────────────┘
```

---

## 3. Funktionen

### 3.1 Projekt und Speicherung

- Ein Projekt ist ein Ordner mit `project.json` (Konfiguration), `codebook.txt` (Kopie), `data.xlsx` (Kopie), `store.sqlite` (alle Einheiten, Aufrufe, Ergebnisse, Reviews, Audit) und `exports/`.
- SQLite (Standardbibliothek) ist die einzige Quelle der Wahrheit: transaktional, nach Abbruch fortsetzbar, durchsuchbar.
- Jede Eingabe wird gehasht (SHA-256): Datei, gemappte Einheiten, Codebuch, Code-Liste, Prompt-Template.

### 3.2 Datenimport (XLSX)

- Blattauswahl, Kopfzeile, Vorschau. Gelesen wird mit `openpyxl` und `data_only=True`, also Formelwerte statt Formeln.
- **Spalten-Mapping:**
  - **ID** (Pflicht; automatisch erzeugte Zeilennummern nur mit Warnung, weil sie instabil sind)
  - **Text** (eine Spalte; optional mehrere, die beschriftet verkettet werden)
  - **Gruppe** (z. B. Transkript, Person, Fall) und **Reihenfolge**, nötig für Kontextfenster und den holistischen Modus
  - **Sprecher:in** (optional, erscheint im Kontext)
  - **Prompt-Metadaten** (optional, z. B. die Fragestellung bei offenen Survey-Antworten)
  - **Gold-Spalten**: 0 bis k Kodierer:innen, dazu optional eine Konsens-Spalte. Bei Multi-Label ist das Trennzeichen einstellbar (Default `;`).
  - **Subgruppen-Variablen** für die Analyse (optional)
- **Prüfungen:** doppelte IDs, leere Texte, Einheiten, die das Kontextbudget sprengen, Gold-Codes außerhalb der Code-Liste (mit Mapping-Dialog, z. B. `1` → `K1`), nicht konstante Gold-Werte innerhalb einer Gruppe im holistischen Modus.

### 3.3 Codebuch und Code-Liste

- Laden von `.txt`/`.md` in UTF-8. Fallback cp1252 mit Warnung, wegen Windows-Dateien.
- Das Codebuch geht **verbatim** in den Prompt. Das Tool verändert den Text nie.
- **Code-Heuristik** schlägt Codes vor. Es gibt Muster-Presets, eigener Regex ist möglich:
  - Markdown-Überschriften `### K1 — Name`
  - `Code:`/`Kategorie:`-Zeilen
  - Nummerierungen `1.2 Name`
  - Zusätzlich gleicht die Heuristik mit den eindeutigen Werten der Gold-Spalten ab.
- **Bestätigungstabelle** mit den Spalten Kürzel (wird zum Enum-Wert), Label, Skalenniveau (nominal oder ordinal mit Rang), aktiv/inaktiv und `parent` (in v1 ausgeblendet). Die Kürzel sind verbindlich: Daraus entstehen das Ausgabeschema und die Metriken.
- **Restkategorie** zuschaltbar (Kürzel Default `KEIN_CODE`, Label editierbar). Wenn das Codebuch selbst schon eine Restkategorie hat, wird diese verwendet.
- **Token-Anzeige:** Länge des Codebuchs je Modell. Eine Warnung erscheint, wenn Codebuch, Kontext und Antwort das Kontextbudget überschreiten (siehe 3.4).
- **Versionierung:** Jede Änderung von Codebuch oder Code-Liste erzeugt einen neuen Hash mit Diff-Ansicht. Die Historie geht in den Report.

### 3.4 Anbieter und Modelle

**Ollama (lokal)** über die native API `/api/chat`, nicht über `/v1`, weil nur die native API Folgendes bietet:

- `options.num_ctx` explizit setzen. Ollama nutzt sonst ein kleines Standard-Kontextfenster und **kürzt längere Prompts ohne Fehlermeldung**. Das Tool vergleicht deshalb `prompt_eval_count` mit der erwarteten Prompt-Länge und bricht bei Kürzung ab.
- `format` = JSON-Schema (Structured Outputs)
- `seed`, `temperature`
- `think` an/aus bei Thinking-Modellen
- `keep_alive`
- Modell-Digest und Quantisierung über `/api/tags` bzw. `/api/show`, Ollama-Version über `/api/version`

**OpenAI-kompatibel** über `httpx` gegen `/chat/completions` (ohne SDK):

- **Preset GWDG/KISSKI Chat AI.** Base-URL laut GWDG-Doku `https://chat-ai.academiccloud.de/v1`, beim Einrichten prüfen. Ebenfalls ein Preset: Datenschutz-Stufe „akademisch, Deutschland“.
- **Preset KI:connect.NRW** (`https://chat.kiconnect.nrw/api/v1`, akademisch). Die Modellnamen weichen von den Kartennamen ab (etwa `OpenAI GPT OSS 120B`); das Abnahmeskript schlägt ähnliche Namen vor. Laut Modellübersicht ignoriert der Anbieter die Temperatur bei gpt-oss-120b und den GPT-5-Modellen.
- **Temperatur „nicht senden“** je Modell: für Anbieter, die sie festlegen. Report und Präregistrierung nennen dann „nach Vorgabe des Anbieters“ statt einer Zahl, die nicht galt.
- **Herleitungsaufwand** (`reasoning_effort`) je Modell, nur OpenAI-kompatibel: Teil der Lauf-Identität, in Report und Präregistrierung.
- **Eigene Anbieter:** Name, Base-URL, Auth-Art (Bearer, `api-key`-Header, keine), Key, Modellliste (über `/models` oder manuell), Rate-Limit (Anfragen pro Minute), Datenschutz-Stufe (lokal / akademisch EU / extern), optional ein Preis pro 1.000 Token.

**Fähigkeits-Probe** beim Einrichten: Mit Mini-Anfragen prüft das Tool, ob `response_format: json_schema`, `json_object`, `seed`, `n` und Thinking unterstützt werden. Die Fallback-Kette lautet `json_schema` → `json_object` + Schema im Prompt → Freitext + JSON-Extraktion. **Jeder Fallback wird protokolliert und im Report ausgewiesen**, weil er die Messbedingungen ändert.

**API-Keys** liegen nur im OS-Schlüsselbund (`keyring`, unter Windows der Credential Manager), nie in `project.json`, Logs oder Exporten.

**Bestätigungsdialog vor API-Läufen:** Er zeigt Anbieter, Host, Datenschutz-Stufe, Anzahl Einheiten, geschätzte Token und den Hinweis zur Pseudonymisierung. Die Zustimmung wird mit Zeitstempel ins Audit-Log geschrieben.

**Modell-Metadaten im Log:**

- Ollama: Name, Digest, Quantisierung, Parametergröße, Ollama-Version
- API: das vom Server **zurückgegebene** Modell (z. B. eine datierte Version), `system_fingerprint` falls vorhanden, Host, Datum
- Ändert sich das zurückgegebene Modell während eines Laufs, warnt die Auswertung; der Report listet die gemeldeten Namen mit Häufigkeit.

### 3.5 Kodier-Engine

- **Einheiten-Modi:**
  - *isoliert*: eine Zeile
  - *Kontextfenster*: n Zeilen davor und m danach aus derselben Gruppe, sortiert nach der Reihenfolge-Spalte. Default 2/1. Kodiert wird nur der Zieltext.
  - *holistisch*: eine Gruppe als Einheit, Zeilen nummeriert
- **Ablaufreihenfolge modellweise** (Modell → Einheit → Lauf), damit bei 4 GB VRAM nie zwischen Modellen gewechselt werden muss. Jedes Modell bleibt per `keep_alive` geladen, solange sein Block läuft.
- **N Läufe pro Einheit und Modell** (Default N = 5) mit Seeds `base_seed + i`, je Lauf ein Request. Mehrere Antworten pro Request (`n`) bewusst nicht (2026-10-08): Alle Läufe hätten dieselbe Code-Reihenfolge, und GWDG/KI:connect dokumentieren `n` nicht.
- **Adaptives N** (Default an): Nach 2 einstimmigen Läufen (gleiche Entscheidung) bricht das Tool für diese Einheit ab (vgl. Aggarwal et al. 2023; Li et al. 2024). Spart Laufzeit und Kontingent bei klaren Fällen. Die Regel wird berichtet. Bis 2026-10-08 war der Default 3; gespeicherte Projekte behalten ihren Wert. Grund für 2: In der KI:connect-Abnahme (gpt-oss-120b 200, qwen3.8-27b 116 Einheiten) hätte der Stopp nach 2 keine Entscheidung geändert und etwa ein Drittel der Aufrufe gespart.
- **Code-Reihenfolge** in der Code-Liste des Prompts wird pro Lauf per Seed permutiert, gegen Positions-Bias (Zheng et al. 2024; Pezeshkpour & Hruschka 2024). Das Codebuch selbst bleibt unverändert.
- **Fortsetzen nach Abbruch:** Vor jedem Aufruf prüft das Tool, ob für (Einheit, Modell, Lauf, Instrument-Hash) schon ein Ergebnis vorliegt.
- **Fehlerbehandlung:**
  - Netzwerkfehler: exponentielles Backoff
  - HTTP 429: `Retry-After` respektieren, Drosselung gemäß Rate-Limit
  - Parse- oder Evidenzfehler: genau **ein** Repair-Versuch mit Fehlermeldung an das Modell. Danach wird die Einheit als Fehler markiert, nichts wird still verworfen.
- **Trockenlauf:** Prompts rendern, Token zählen, Laufzeit schätzen, ohne einen einzigen Aufruf. Aufrufe und Eingabe-Token als Spanne (adaptiver Stopp), die Ausgabe nach den bisherigen Antworten der Modelle.
- **Kosten und Kontingent** (2026-10-08, User: Studie über KI:connect und GWDG, „baue alles was sich lohnt“). Gemessen in der KI:connect-Abnahme: je Aufruf ~63 % System-Prompt mit Codebuch, ~23 % Reasoning, ~8 % sichtbare Antwort, ~6 % Zieltext mit Kontext. Umgesetzt: Stopp nach 2 einstimmigen Läufen; Verbrauch (Token, gecacht, Reasoning) und Restkontingent aus den `x-ratelimit`-Kopfzeilen nach jedem Lauf, Warnung vor dem Lauf, wenn das zuletzt gemeldete Tages- oder Monatskontingent nicht reicht; Sensitivitätsvariante „Reasoning niedrig“ für denkende Modelle. Nicht umgesetzt: Batch-Prompting (Cheng et al. 2023; Instrument ändert sich stark, für Codebuch-Analyse ungeprüft), `n`, Batch-APIs (bei GWDG/KI:connect nicht verfügbar), Kaskaden im Panel (Chen et al. 2024; Panel-Kennwerte nur noch auf schwierigen Einheiten), `prompt_cache_key` und Anthropic-Caching (bei den genutzten Anbietern ohne Kostenwirkung).
- **Pilot:** Die ersten 20 Einheiten werden kodiert. Aus der gemessenen Zeit pro Aufruf rechnet das Tool die ETA für den Gesamtlauf hoch.
- Pause, Abbruch und Fortsetzen. Während eines Laufs wird der Energiesparmodus blockiert (Windows `SetThreadExecutionState`), damit Läufe über Nacht durchkommen.

### 3.6 Ausgabe-Validierung und Evidenzprüfung

- Pydantic-Modell pro Modus. Geprüft werden:
  - Codes aus dem Enum
  - keine Duplikate
  - höchstens k Kandidaten
  - Konfidenzen von 0 bis 100
  - bei Single-Label eine Summe ≤ 100. Bei Verletzung wird normalisiert und markiert.
  - bei Multi-Label ist `KEIN_CODE` exklusiv
- **Constraints, die ein Backend im Schema nicht unterstützt** (z. B. `maxLength`, `maxItems`), werden nachgelagert in Pydantic geprüft.
- **Signalwort-Verifikation** in Stufen:
  1. exakter Treffer im Zieltext → Zeichen-Offsets (Start, Ende)
  2. normalisierter Treffer: Unicode NFC, Whitespace, typografische Anführungszeichen und Bindestriche, Groß/Klein → Offsets, Status „normalisiert“
  3. unscharfer Treffer (`rapidfuzz`, partial ratio ≥ 90) → Status „unscharf“
  4. kein Treffer → Status „nicht belegt“, löst den Repair-Versuch aus
- Bei mehreren Vorkommen gilt das erste, die Mehrdeutigkeit wird vermerkt.
- Im Kontextmodus zählen **nur Treffer im Zieltext**. Ein Treffer nur im Kontext gilt als Fehler.
- Die **Evidenz-Trefferquote** pro Modell ist ein eigener Kennwert (Abschnitt 6, F).

### 3.7 Aggregation und Konfidenz

Siehe Abschnitt 5. Gespeichert werden pro Einheit:

- finale Codes (Top-k)
- Self-Consistency-Konfidenz `p_sc` und verbale Konfidenz `p_verbal` je Kandidat
- normalisierte Entropie
- Panel-Stimmen je Modell
- Signalwörter mit Spans und Häufigkeit
- die repräsentative Begründung
- Flags: unentschieden, Dissens, Abstain, nicht belegt, Parse-Fehler, Fallback

### 3.8 Splits, Freeze und Präregistrierung

- **Split** nur über Einheiten mit Gold. Default Dev 25 % / Test 75 %. Stratifiziert nach Gold-Code (Konsens, sonst Kodierer:in 1), im Kontext- und Holistikmodus **gruppenweise**, damit keine Gruppe in beiden Teilen liegt. Der Split-Seed wird gespeichert.
- **Dev-Schleife:** Kodieren und Kennwerte nur auf Dev. Test-Ergebnisse sind gesperrt, solange nicht eingefroren ist.
- **Freeze** bildet den **Instrument-Hash** = SHA-256 über:
  - Codebuch-Text und Code-Liste
  - Prompt-Template mit Version
  - Panel (Modelle mit Digest bzw. Version)
  - alle Parameter, Einheiten-Modus, Aggregations- und Gate-Regeln
- Erst nach dem Freeze lässt sich der Test auswerten. **Jede** Test-Auswertung wird mit Hash protokolliert. Der Report nennt ihre Anzahl. Ändert sich nach dem Freeze etwas, gilt der Freeze als gebrochen, mit sichtbarer Warnung im Report.
- **Präregistrierungs-Export** (Markdown/PDF): eingefrorene Konfiguration, vollständiger Prompt, Analyseplan (welche Kennwerte, welche Schwellen), Split-Größen. Fertig zum Hochladen bei OSF.

### 3.9 Review-Queue

- **Gate-Kriterien** (einstellbar):
  - `p_panel(top1)` < τ (Default 0,6)
  - Panel-Dissens
  - Abstain
  - nicht belegte Evidenz
  - Parse-Fehler
- **Zufallsstichprobe** zusätzlich: x % aller Einheiten mit **protokollierter Ziehwahrscheinlichkeit**. Nur so bleibt die bias-korrigierte Schätzung gültig (Egami et al. 2023; Gligorić et al. 2025).
- **Blindmodus** (Default): Die Person sieht Text und Codebuch, aber keinen LLM-Vorschlag. Sichtbare LLM-Labels verzerren menschliche Urteile (Wang et al. 2024). Nur blinde Reviews zählen als Gold.
- **Assistierter Modus:** Die Person korrigiert den LLM-Vorschlag. Das Ergebnis heißt „LLM-gestützt korrigiert“ und fließt **nicht** in die Validierungskennwerte ein.
- Name der Person, Zeitstempel, Tastaturkürzel. Reviews werden als eigene Annotationsschicht gespeichert und überschreiben nie die LLM-Ausgabe.

### 3.10 Evaluation

Vollständige Kennwertliste in Abschnitt 6. Die Oberfläche bietet:

- **Vergleichsauswahl:** LLM-Panel bzw. einzelne Modelle gegen jede:n Kodierer:in, gegen den Konsens und gegen alle. Dazu die Baselines Mensch-Mensch, Modell-Modell und Lauf-Lauf.
- **Teilmenge:** Dev / Test (nur nach Freeze) / alle / Subgruppe
- **Tabellen** pro Code und gesamt, alle Werte mit 95-%-Bootstrap-KI
- **Plots:** Konfusionsmatrix, Reliability-Diagramm, Prävalenzvergleich, Risk-Coverage-Kurve
- **Warnung bei geringer Stützung:** Codes mit wenigen Gold-Positiven (Default n < 20) werden markiert, ihr KI wird hervorgehoben.

### 3.11 Bias-korrigierte Schätzung

- Code-Prävalenzen (Anteil je Code) über **PPI++** mit Power-Tuning (eigene Implementierung, Angelopoulos, Duchi & Zrnic 2023). LLM-Labels aller Einheiten werden mit der zufälligen Gold-Teilmenge kombiniert und ergeben einen unverzerrten Schätzer mit KI; im Kontextmodus cluster-robust über die Transkripte (Einflussterme je Cluster, CR1), per Simulation geprüft.
- Stammen die Gold-Labels aus dem Gate statt rein zufällig, wird mit den protokollierten Ziehwahrscheinlichkeiten gewichtet (confidence-driven inference, Gligorić et al. 2025).
- Export für das R-Paket `dsl` (Egami et al.) für Regressionsanalysen, in Phase 4.

### 3.12 Export

| Format | Inhalt |
|---|---|
| **XLSX (wide)** | Originaldaten + `llm_code_1..k`, `p_sc_1..k`, `p_verbal_1..k`, `begruendung`, `signalwoerter`, `evidenz_status`, `entropie`, `panel_einig`, Flags, Review-Status, Split |
| **XLSX/CSV (long)** | eine Zeile pro Einheit × Modell × Lauf, mit Roh-Kandidaten, für eigene Analysen |
| **SPSS .sav / Stata .dta** | über `pyreadstat`, mit Wertelabels (Code-Kürzel → Label) und Variablenlabels |
| **R .rds** | über `pyreadr`, Codes als Faktor |
| **REFI-QDA .qdpx** | Projekt mit Quellen, Codesystem, Kodierungen und Memos (Details unten) |
| **REFI-QDA .qdc** | nur das Codesystem |
| **Report HTML + DOCX** | siehe 3.13 |
| **Replikationspaket .zip** | siehe 3.13 |

**REFI-QDA-Abbildung:**

- **Quellen:** eine `TextSource` pro Gruppe. Die Einheiten stehen zeilenweise mit Kopfzeile `[ID]` darin. Ohne Gruppe gibt es eine `TextSource` pro Einheit.
- **Users:** „LLM-Panel“ (optional zusätzlich ein User pro Modell) und **jede:r menschliche Kodierer:in als eigener User**. Damit rechnen MAXQDA und ATLAS.ti die Intercoder-Übereinstimmung LLM gegen Mensch in ihrer eigenen Funktion nach.
- **Codesystem:** die bestätigten Codes (+ `KEIN_CODE`) und ein **separater Zweig „LLM-Evidenz › ‹Code›“** für die Signalwort-Spans. Die Spans verfälschen so nicht den segmentbasierten Intercoder-Vergleich der Hauptcodes.
- **Kodierungen:** Die ganze Einheit erhält den Top-1-Code, bei Multi-Label alle finalen Codes. Die Signalwort-Spans bekommen den Evidenz-Code.
- **Memo pro Einheit:** Top-k mit beiden Konfidenzen, Begründung (gekennzeichnet als LLM-Plausibilisierung), Flags.
- **Eigener Writer** (`lxml`) mit Validierung gegen das offizielle REFI-QDA-XSD. `pyqdpx` und `qdpxlib` werden geprüft, ihr Reifegrad ist aber offen.
- **Abnahme:** Import in MAXQDA, ATLAS.ti und QualCoder. NVivo nach bestem Bemühen, sein Import gilt als lückenhaft.
- **Risiko Offset-Zählung:** Zeichen vs. UTF-16-Einheiten bei Emojis und kombinierenden Zeichen. Das ist ein expliziter Testfall.

### 3.13 Report und Replikationspaket

**Methoden-Report** (HTML über Jinja2, DOCX über python-docx). Die Gliederung orientiert sich an Törnberg 2024 und TRIPOD-LLM 2025:

1. Daten: N, Einheit, Kontextregel, Gruppen, Split-Größen
2. Codebuch: Hash, Token-Länge, Codes, Versionshistorie
3. Modelle: Name, Anbieter, Version bzw. Digest, Quantisierung, lokal oder API, Datum
4. Parameter: Temperatur, Seed-Schema, N und adaptive Regel, num_ctx, Thinking, Code-Permutation, Fallbacks
5. Vollständiger Prompt (Anhang) und ein gerendertes Beispiel
6. Aggregation und Gate
7. Validierungsdesign: Freeze-Datum, Anzahl Test-Auswertungen, Präregistrierung
8. Ergebnisse: alle Kennwerte mit KI, pro Code, Mensch-Mensch-Baseline, Alt-Test
9. Stabilität, Kalibrierung, Parse- und Evidenzquoten, Subgruppen
10. Ressourcen: Token, Laufzeit, Kosten. Software-Versionen.
11. **Automatischer Methodenabsatz** in DE und EN als Textbaustein mit eingesetzten Werten, zum Übernehmen ins Paper
12. **GUIDE-LLM-Checkliste** (Feuerriegel et al. 2026): 14 Kernpunkte und Zusatzangaben, vorausgefüllt aus Projekt, Audit-Log und Auswertung; auch als `guide_llm.md` im Paket

**Replikationspaket (.zip):**

- `project.json`, `codebook.txt`, `codes.csv`, Prompt-Templates
- `calls.jsonl` mit allen Requests und Responses ohne Keys
- Ergebnisse, `metrics.json`
- `environment.txt` (Paketversionen, Ollama-Version, Betriebssystem)
- README mit Rerun-Anleitung
- `guide_llm.md` (GUIDE-LLM-Checkliste)
- Datenschutz-Schalter: **Rohtexte enthalten ja/nein**. Bei „nein“ nur IDs und Hashes.

### 3.14 Audit-Log

Append-only in SQLite, exportierbar als JSONL. Ein Eintrag pro Ereignis:

- jeder Aufruf mit Parametern, Prompt-Hash, Roh-Antwort, Status, Retries, Token, Latenz
- Freeze und Test-Auswertungen
- API-Zustimmungen
- Codebuch-Versionen
- Reviews

---

## 4. Prompt-Design

### 4.1 Aufbau

- **System-Nachricht:** Rolle und Regeln → Codebuch verbatim → Code-Liste (permutiert) → Ausgabeanweisung.
  **User-Nachricht:** Kontext und Zieltext.
- Statisches kommt zuerst, Variables zuletzt. Dann bleibt das lange Codebuch ein **identisches Präfix**, und Ollama (KV-Cache) wie OpenAI-kompatible Server (Prompt-Caching) verarbeiten es nicht jedes Mal neu. Bei 4 GB VRAM ist das der größte Laufzeithebel.
- Klare Begrenzer (`<codebuch>`, `<zieltext>`, `<kontext_davor>`) trennen Instrument und Daten. Anweisungen im Datentext werden dadurch weniger leicht als Befehle gelesen.
- Templates gibt es auf **DE und EN**, passend zur Sprache des Codebuchs. Die Template-Version geht in den Instrument-Hash ein.

### 4.2 Template-Entwurf (DE, Single-Label)

```text
[system]
Du kodierst Texte in einer deduktiven Inhaltsanalyse. Grundlage ist ausschließlich
das folgende Codebuch. Wende seine Definitionen, Kodierregeln und Abgrenzungen
wörtlich an und bringe kein eigenes Vorwissen über die Kategorien ein.

<codebuch>
{codebuch_verbatim}
</codebuch>

Zulässige Codes (nur diese Kürzel verwenden; die Reihenfolge ist zufällig und
bedeutet nichts):
{code_liste_permutiert}          # "- K3: Label"
- KEIN_CODE: Nach dem Codebuch trifft keiner der Codes zu oder der Text ist nicht
  kodierbar.                     # nur wenn Restkategorie aktiv

Vorgehen:
1. Lies den Zieltext. Kontext dient nur dem Verständnis; kodiert wird
   ausschließlich der Zieltext.
2. Prüfe die in Frage kommenden Codes gegen die Kriterien des Codebuchs.
3. Begründe deine Entscheidung knapp (höchstens {max_woerter} Wörter) mit Bezug
   auf das Codebuch.
4. Nenne den am besten passenden Code und bis zu {k_minus_1} plausible
   Alternativen, absteigend sortiert.
5. Gib für jeden genannten Code 1 bis {max_sw} Signalwörter an: Wörter oder kurze
   Wortgruppen (höchstens 6 Wörter), die du zeichengenau aus dem Zieltext kopierst
   und die für diesen Code sprechen. Nichts umformulieren, nichts aus dem Kontext.
6. Gib für jeden Code eine Konfidenz von 0 bis 100 an: die Wahrscheinlichkeit,
   dass dieser Code der richtige ist. Die Summe ist höchstens 100.

Antworte ausschließlich mit JSON nach dem vorgegebenen Schema.

[user]
<kontext_davor>
[{id}] {sprecher}: {text}
…
</kontext_davor>
<zieltext id="{unit_id}">
{text}
</zieltext>
<kontext_danach>
…
</kontext_danach>
```

**Multi-Label-Variante:**

- Schritt 4 lautet: „Nenne alle zutreffenden Codes, höchstens {k}, absteigend nach Sicherheit. KEIN_CODE nur allein.“
- Schritt 6 lautet: „…die Wahrscheinlichkeit, dass dieser Code zutrifft.“ Es gibt keine Summenregel.

**Holistisch:** Statt `<zieltext>` gibt es `<einheit id=…>` mit nummerierten Zeilen. Signalwörter dürfen aus allen Zeilen stammen.

### 4.3 Ausgabeschema

Die **Reihenfolge der Felder ist Absicht**: erst Begründung, dann pro Kandidat erst die Belege, dann Code, dann Konfidenz. Das Modell legt sich so erst nach einer kurzen Begründung fest. Strikte Formate verschlechtern sonst nachweislich das Schließen (Tam et al. 2024). Constrained Decoding hält die Schema-Reihenfolge ein.

Die Feldnamen sind englisch und damit unabhängig von der Prompt-Sprache. So bleibt das gespeicherte Datenformat für deutsche und englische Prompts gleich.

```json
{
  "type": "object",
  "properties": {
    "rationale": { "type": "string" },
    "candidates": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "evidence":   { "type": "array", "items": { "type": "string" } },
          "code":       { "type": "string", "enum": ["K1", "K2", "K3", "KEIN_CODE"] },
          "confidence": { "type": "integer" }
        },
        "required": ["evidence", "code", "confidence"],
        "additionalProperties": false
      }
    }
  },
  "required": ["rationale", "candidates"],
  "additionalProperties": false
}
```

Längen, Anzahlen und Wertebereiche werden zusätzlich in Pydantic geprüft (3.6), weil nicht jedes Backend diese Schema-Keywords durchsetzt.

### 4.4 Parameter und Defaults

| Parameter | Default | Begründung |
|---|---|---|
| Temperatur | 0,7 bei N > 1; 0 bei N = 1 | Self-Consistency braucht Streuung (Wang et al. 2023). T = 0 ist ohnehin nicht deterministisch (Atil et al. 2024). |
| top_p | 1,0, unverändert | Nur eine Zufallsquelle wird variiert. |
| Seed | `base_seed + lauf_index` | Reproduzierbar, soweit das Backend es erlaubt. Wird immer protokolliert. |
| N Läufe | 5, adaptiv: Stopp nach 2 einstimmigen (bis 2026-10-08: 3) | Zuverlässigkeit vs. Laufzeit auf 4 GB VRAM (Reiss 2023; Aggarwal et al. 2023) |
| k (Top-k) | 3 (einstellbar 1 bis 3) | Vorgabe |
| Signalwörter je Code | max. 3, je ≤ 6 Wörter | prüfbar, wenig Halluzinationsfläche |
| Begründung | ≤ 40 Wörter | Kosten, Fokus; Begründung als knappe Vorstufe zur Entscheidung (Dunivin 2024) |
| num_ctx (Ollama) | automatisch: Prompt-Token + max_tokens + 10 %, mind. 4.096 | Schutz vor stiller Kürzung |
| max_tokens | 512 ohne Thinking; mit Thinking einstellbar | |
| Thinking | **Pflichtangabe** an/aus pro Modell | Vergleichbarkeit und Laufzeit. Nie implizit. |
| Code-Permutation | an, per Seed | Positions-Bias (Zheng et al. 2024) |
| Kontextfenster | 2 davor, 1 danach, gleiche Gruppe | Kontextfehler sind bei Dialogen der häufigste Fehlermodus. |
| Restkategorie | an | Erzwungene Wahl erhöht Falsch-Positive. |
| Repair-Versuche | 1 | Mehr Versuche verschleiern Modellschwächen. |
| Gate-Schwelle τ | 0,6 | Auf Dev prüfen und vor dem Freeze festlegen. |

### 4.5 Bewusst nicht enthalten

- keine Prompt-Optimierung gegen Gold-Daten (DSPy o. Ä.). Falls später, dann nur auf Dev und mit Freeze.
- keine Beispiele aus den Daten in v1
- keine Persona- oder Belohnungs-Tricks im Prompt
- kein stilles Umformulieren des Codebuchs

---

## 5. Konfidenz und Aggregation

Notation: Einheit *u*, Modell *m*, Läufe *r* = 1…N_m (bei adaptivem Stopp gilt N_m ≤ N), Codemenge *C*.

**Single-Label**

- Self-Consistency pro Modell: `p_sc_m(c) = (1/N_m) · Σ_r 1[top1_{m,r} = c]`
- Panel (gleich gewichtet): `p_panel(c) = (1/|M|) · Σ_m p_sc_m(c)`
- Verbal: `p_verbal(c)` = Mittel der genannten Konfidenzen/100 über alle Läufe und Modelle. Nicht genannt zählt als 0.
- **Top-k** nach `p_panel`. Bei Gleichstand entscheidet `p_verbal`. Die übrigen Plätze werden aus der Rangfolge der Alternativen aufgefüllt, als mittlerer Rang über alle Läufe.
- Normalisierte Entropie `H = −Σ p log p / log |C|` als Unsicherheitsmaß.
- **Finaler Code** = argmax `p_panel`. Flag „unentschieden“, wenn die beiden Spitzenwerte innerhalb von ε = 0,1 liegen.

**Multi-Label**

- `p_sc_m(c)` = Anteil der Läufe, die *c* als zutreffend nennen.
- Finale Menge = alle *c* mit `p_panel(c) ≥ 0,5`, höchstens k, absteigend.
- `KEIN_CODE` gilt nur, wenn kein anderer Code die Schwelle erreicht.

**Repräsentative Ausgabe**

- Begründung: aus dem ersten Lauf (in Seed-Reihenfolge), dessen Top-1 dem finalen Code entspricht. Das ist deterministisch.
- Signalwörter: Vereinigung aus allen Läufen, die den Code gewählt haben, sortiert nach Häufigkeit.

**Optional (v1.x):**

- Dawid-Skene statt gleichgewichteter Mittelung (Crowd-Kit)
- Rekalibrierung der Konfidenzen auf Dev (isotonisch)

Welche Konfidenz besser kalibriert ist, entscheidet sich empirisch auf Dev (Abschnitt 6, E).

---

## 6. Kennwerte

Jeder Kennwert kommt mit 95-%-Bootstrap-KI (Hayes & Krippendorff 2007; 1.000 Resamples, einstellbar). Im Kontext- und Holistikmodus wird **cluster-gebootstrappt** nach Gruppe.

Interpretationshilfen erscheinen mit Quelle und Vorbehalt: Landis & Koch 1977 für κ, Krippendorff 2018 für α (≥ 0,800 belastbar, ≥ 0,667 vorläufig).

| Bereich | Kennwert | Wann | Implementierung |
|---|---|---|---|
| **A Übereinstimmung** | Prozentuale Übereinstimmung | immer, nie allein | eigen |
| | Cohens κ | 2 Kodierende, nominal | scikit-learn |
| | Gewichtetes κ (linear, quadratisch) | ordinale Codes | scikit-learn |
| | Scotts π | 2 Kodierende | irrCAC |
| | Fleiss' κ | ≥ 3 Kodierende | statsmodels / irrCAC |
| | Krippendorffs α (nominal, ordinal; MASI für Multi-Label) | beliebig viele Kodierende, fehlende Werte | `krippendorff` + eigene MASI-Distanz |
| | Gwets AC1 / AC2 | robust bei schiefer Prävalenz (κ-Paradox) | irrCAC |
| | PABAK / Brennan-Prediger + Prävalenz- und Bias-Index | erklärt niedrige κ trotz hoher Übereinstimmung (Byrt et al. 1993) | irrCAC / eigen |
| **B Klassifikation** (LLM vs. Gold) | Accuracy, Balanced Accuracy | immer | scikit-learn |
| | Precision, Recall, F1 je Code + Support | immer | scikit-learn |
| | Macro-, Micro-, Weighted-F1 | immer | scikit-learn |
| | MCC (multiklassig) | immer (Chicco & Jurman 2020) | scikit-learn |
| | Konfusionsmatrix (absolut, zeilennormiert) | immer | scikit-learn + matplotlib |
| | Top-2- / Top-3-Accuracy | Single-Label, k > 1 | eigen |
| | Abstain-Rate, Coverage, selektive Accuracy, Risk-Coverage-Kurve | Restkategorie aktiv | eigen |
| **C Multi-Label** | κ und P/R/F1 je Code (binär), Micro/Macro | Multi-Label | scikit-learn |
| | Hamming-Loss, Exact Match, Jaccard (Stichprobenmittel) | Multi-Label | scikit-learn |
| **D Prävalenz** | Code-Verteilung LLM vs. Mensch, Δ mit KI | immer | eigen |
| | Stuart-Maxwell-Test (marginale Homogenität), McNemar je Code | systematische Verschiebung prüfen | statsmodels |
| | PPI-korrigierte Prävalenz mit KI | Gold-Zufallsstichprobe vorhanden | ppi_py |
| **E Kalibrierung** | ECE, Brier-Score, Reliability-Diagramm, AUROC (Konfidenz trennt richtig von falsch) | je für `p_sc` und `p_verbal` | eigen + scikit-learn |
| **F LLM-Stabilität** | Intra-Modell-α über Läufe, Inter-Modell-α im Panel, Test-Retest-α über Termine | immer bzw. bei Wiederholung | `krippendorff` |
| | Parse-Fehler-, Fallback- und Repair-Quote, Evidenz-Trefferquote | immer | eigen |
| **G Ersetzbarkeit** | LLM-Mensch-Werte relativ zur Mensch-Mensch-Baseline | ≥ 2 Kodierer:innen | eigen |
| | Alt-Test: Winning Rate ω, Advantage Probability (Calderon et al. 2025) | ≥ 3 Kodierer:innen | eigen, gegen die Referenzimplementierung der Autor:innen geprüft |
| **H Subgruppen** | κ, F1, Prävalenz je Ausprägung einer gewählten Variable | Subgruppen-Spalte gesetzt | eigen |
| **I Differentielle Messfehler** | Wald-Test Fehler ~ Ausprägung im linearen Wahrscheinlichkeitsmodell, CR1/HC1, F-Verteilung, Holm über die Variablen; Warnung bei p < 0,05 | Subgruppen-Variable vorhanden | eigen, Egami et al. 2023 |

Jede Metrik wird gegen **publizierte Referenzwerte** getestet (Abschnitt 11). Ohne bestandene Tests gibt es keinen Release.

---

## 7. Hardware-Realität: 4 GB VRAM

**Rechnung** (grob, wird im Pilot gemessen): 2.000 Einheiten × 5 Läufe = 10.000 Aufrufe pro Modell. Bei grob 2 bis 10 s pro Aufruf auf einem 4B-Modell sind das **etwa 6 bis 28 Stunden pro lokalem Modell**. Ein rein lokales Dreier-Panel bräuchte also Tage.

**Konsequenzen im Design:**

1. **Lokal laufen Modelle bis ca. 4B in Q4.** Sie belegen ca. 2,5 bis 3 GB, der Rest ist KV-Cache. Lange Codebücher (> 4.000 bis 6.000 Token) erzwingen CPU-Offload und vervielfachen die Laufzeit. Das Tool zeigt die Token-Länge und warnt. Tipp in der Doku: `OLLAMA_KV_CACHE_TYPE=q8_0` halbiert den KV-Cache-Bedarf.
2. **Panel-Default:** 1 lokales Modell + 1 bis 2 Modelle über GWDG (nach Bestätigung). Möglichst unterschiedliche Modellfamilien; das hilft nur begrenzt, denn auch Modelle verschiedener Familien stimmen untereinander stärker überein als mit Menschen (Stolwijk et al. 2025). Rein lokal geht auch, dann als Nachtlauf.
3. **Adaptives N** halbiert die Aufrufe bei klaren Fällen ungefähr.
4. **Modellweise Reihenfolge** und `keep_alive`: kein Nachladen zwischen Einheiten.
5. **Präfix-Caching:** Das Codebuch steht vorn und bleibt identisch, nur Code-Liste und Einheit ändern sich.
6. **Pilot mit ETA** vor jedem Vollauf. Die Dev-Schleife läuft nur auf Dev (ca. 25 % der Gold-Einheiten).
7. Die GWDG-Rate-Limits werden über Drosselung und Backoff eingehalten.

---

## 8. Architektur und Technik

### 8.1 Paketstruktur

```
llm-annotation/
├─ pyproject.toml              # uv, Python ≥ 3.12
├─ src/llmanno/
│  ├─ core/                    # ohne Qt, vollständig testbar
│  │  ├─ config.py             # Projektkonfiguration (pydantic), read_config
│  │  ├─ project.py            # Projekt laden/speichern, Import, Gold-Nachtrag, Sperre
│  │  ├─ store.py              # SQLite: Einheiten, Aufrufe, Reviews, Split, Audit
│  │  ├─ data_import.py        # XLSX/CSV → Einheiten, Zusammenführen, Prüfbericht
│  │  ├─ codebook.py           # Laden, Code-Heuristik, Hash
│  │  ├─ history.py            # Codebuch-Fassungen
│  │  ├─ units.py              # drei Einheiten-Modi, Kontextfenster, Zielspalte
│  │  ├─ prompt.py             # Templates DE/EN, Rendering, Token-Schätzung
│  │  ├─ schema.py             # JSON-Schemata der Antwort
│  │  ├─ providers/            # base, ollama, openai_compat, Schlüsselbund, Probe
│  │  ├─ engine.py             # Lauf, adaptives N, Retry, Resume, Pilot/ETA, Fingerabdruck
│  │  ├─ evidence.py           # Signalwort-Verifikation, Offsets
│  │  ├─ aggregate.py          # Self-Consistency, Panel, Entropie, Finalcode
│  │  ├─ validation.py         # Dev/Test-Split, Freeze, Instrument-Hash, Test-Sperre
│  │  ├─ gold.py, review.py    # Gold-Zuordnung; Gate, Queue, Ziehwahrscheinlichkeiten
│  │  ├─ evaluation.py         # alle Kennwerte einer Auswertung
│  │  ├─ sensitivity.py        # Prompt-Sensitivität
│  │  ├─ transfer.py, lock.py  # Instrument übertragen; Projektsperre
│  │  ├─ metrics/              # agreement, classification, calibration, prevalence,
│  │  │                        # alt_test, differential, bootstrap
│  │  └─ export/               # xlsx, stats (sav/dta/rds), refi, report, plots, prereg,
│  │                           # guide (GUIDE-LLM), evaluation, replication
│  ├─ gui/                     # PySide6: main_window, ein Modul je Tab, common (Task)
│  ├─ i18n/                    # .ts / .qm
│  └─ rerun.py                 # llmanno-rerun
├─ tests/                      # pytest, pytest-qt; test_golden hält Hashes fest
├─ examples/                   # Beispieldaten + Beispiel-Codebuch
├─ packaging/                  # PyInstaller-Spezifikation, Lizenztexte für die Exe
└─ docs/
```

Die strikte Trennung `core` ↔ `gui` hat zwei Gründe: Der Kern ist ohne Oberfläche testbar, und ein **Headless-Rerun** für Replikationen (`llmanno-rerun <paket.zip> --out <ordner> [--data <daten>] [--consent]`, Phase 4) kostet kaum Zusatzaufwand.

### 8.2 Bibliotheken

| Zweck | Bibliothek | Grund |
|---|---|---|
| GUI | **PySide6-Essentials** | offizielles Qt-Binding unter LGPL, damit ist die Lizenz bei der Veröffentlichung frei wählbar (gewählt: AGPL-3.0). PyQt6 wäre GPL oder kommerziell. Essentials reicht für alle Module (auch QtSvg) und hält den Installer klein. |
| Threads | `QThread`-Worker + Signale | Lauf blockiert die Oberfläche nicht |
| XLSX, CSV | `openpyxl`, `csv` (Standardbibliothek), `pandas` für den Statistik-Export | |
| Datenmodelle | `pydantic` v2 | Validierung + JSON-Schema-Erzeugung für Structured Outputs |
| Ollama | `httpx` gegen die native API | num_ctx, format, think, keep_alive, Digest |
| OpenAI-kompatibel | `httpx` gegen `/chat/completions` | eine Abhängigkeit statt zwei SDKs; Fallbacks und Retries vollständig unter eigener Kontrolle; testbar per `MockTransport` |
| Schlüssel | `keyring` | Windows Credential Manager |
| Unscharfe Evidenz | `rapidfuzz` | |
| Metriken | eigene Implementierung mit `numpy` und `scipy` | keine schweren Abhängigkeiten im Installer; Tests prüfen gegen `scikit-learn` und `statsmodels` (nur Entwicklung) und gegen veröffentlichte Werte |
| PPI | eigene Implementierung (geschlossene Form nach Angelopoulos et al. 2023) | keine schwere Abhängigkeit für eine Formel; per Simulation auf Unverzerrtheit und Abdeckung getestet |
| Grafiken | SVG aus der Standardbibliothek (`core/export/plots.py`) | im HTML-Report eingebettet, in der Oberfläche über QtSvg; kein `matplotlib` |
| Report | HTML aus der Standardbibliothek, DOCX über `python-docx` | eine Gliederung, zwei Renderer |
| Statistik-Export | `pyreadstat` (.sav, .dta), `pyreadr` (.rds) | |
| QDA | `xml.etree` (Standardbibliothek) | Tests validieren gegen die offiziellen REFI-XSDs (`lxml`, Dev) |
| Werkzeuge | `uv`, `ruff`, `pytest`, `pytest-qt`, `PyInstaller` (`packaging/llmanno.spec`) | |

### 8.3 Datenmodell (Kern)

```
Project    id, name, sprache, modus(single|multi), k, restkategorie, einheiten_modus,
           kontext(n_davor, n_danach), codebook(text, hash, versionen[]),
           codes[Code], panel[ModelConfig], parameter, split, freeze(hash, datum)
Code       kuerzel, label, skalenniveau(nominal|ordinal), rang?, parent?, aktiv
ModelConfig provider_key, modell, thinking, temperatur, max_tokens, num_ctx?, faehigkeiten
Unit       unit_id, gruppe?, reihenfolge?, text, sprecher?, meta{}, gold{kodierer: codes}, split
Call       unit_id, model_key, lauf, seed, instrument_hash, prompt_hash, request_params,
           raw_response, parsed, status, retries, fallback, tokens_in/out, latenz, zeit
Result     unit_id, finale_codes[], p_sc{}, p_verbal{}, entropie, stimmen{}, evidenz[],
           begruendung, flags[]
Review     unit_id, person, modus(blind|assistiert), codes[], zeit, zieh_wkt
Audit      zeit, ereignis, details(json)
```

---

## 9. Oberfläche (Tabs, linear geführt)

1. **Projekt:** neu oder öffnen, Projektordner, Sprache, Modus (Single/Multi), k
2. **Daten:** XLSX-Import, Vorschau, Spalten-Mapping, Prüfbericht, Gold-Code-Mapping
3. **Codebuch:** Viewer, Muster-Preset, Bestätigungstabelle, Restkategorie, Token-Länge, Hash und Versionen
4. **Modelle:**
   - Anbieter-Registry: Ollama automatisch erkannt, GWDG-Preset, „Anbieter hinzufügen“
   - Verbindungstest und Fähigkeits-Probe
   - Panel-Zusammenstellung und Parameter pro Modell
5. **Prompt & Einheiten:** Einheiten-Modus, Kontextfenster, N und adaptive Regel, **vollständige Prompt-Vorschau** für eine gewählte Einheit, Trockenlauf
6. **Splits & Freeze:** Split-Konfiguration, Dev-Kennwerte, Freeze-Knopf mit Instrument-Hash, Präregistrierungs-Export
7. **Lauf:** Pilot, Fortschritt, ETA, Token und Kosten, Fehlerzähler, Pause/Fortsetzen, Live-Log
8. **Ergebnisse:**
   - Tabelle mit Filtern nach Code, Konfidenz, Dissens und Flags
   - Detailansicht: Text mit **hervorgehobenen Signalwörtern**, Kandidaten als Balken (`p_sc` und `p_verbal`), Stimmen je Modell, Begründung, Roh-Antworten
9. **Review:** Queue, Blind- oder Assistenzmodus, Tastaturkürzel, Fortschritt
10. **Evaluation:** Vergleichsauswahl, Kennwerttabellen, Plots, Baselines, Alt-Test, Subgruppen, PPI
11. **Export:** Formate, Datenschutz-Schalter, Report, Replikationspaket

---

## 10. Roadmap

Stand 2026-10-06:
- **P0–P2 fertig.** **P1-Abnahme bestanden** am 2026-10-06, über KI:connect.NRW mit `OpenAI GPT OSS 120B` statt lokal (lokal zu langsam), `scripts/abnahme.py`:
  - 200 Einheiten, 616 Aufrufe: Pilot 60 mit Hochrechnung, Anhalten nach 15, Fortsetzen. 0 Duplikate, eine Lauf-Identität, alle 200 Einheiten mit Ergebnis.
  - 615 gültig, 1 ungültig (als ungültig markiert, die Einheit trägt über die anderen Läufe), 5 repariert, 5 Konfidenzen normalisiert, 0 nicht belegt, 0 abgeschnitten.
  - Adaptiv: 192 Einheiten mit 3 Läufen, 8 mit 5. 603k Token ein, 245k aus, 23 min, Median 2,1 s je Aufruf.
  - κ gegen Coder A 0,98, gegen Coder B 0,83; Coder A gegen B 0,85; Test-Retest-α 0,97. Die Beispieldaten sind synthetisch, die Zahlen prüfen die Mechanik, keine Validität.
  - **Befund:** KI:connect verteilt gpt-oss-120b zufällig auf zwei vLLM-0.30.0-Server mit eigenen Namen (`openai/gpt-oss-120b`, `openai-gpt-oss-120b`) und Fingerprints. Die Modellwechsel-Warnung schlägt an; die Treffer gegen Coder A je Aufruf unterscheiden sich kaum (96,5 % und 98,3 %). Die Temperatur ignoriert der Anbieter laut Modellübersicht; der Lauf lief noch ohne „nicht senden“.
- **P3 umgesetzt:** Panel und Aggregation, Split, Freeze und Präregistrierung (mit Oberfläche), Review-Queue (Gate, Zufallsstichprobe, blind/assistiert, blinde Reviews als Gold-Schicht), Kalibrierung, Alt-Test, Subgruppen.
  - **Panel-Abnahme 2026-10-07, abgebrochen:** über KI:connect.NRW mit `OpenAI GPT OSS 120B` und `qwen3.8-27b` (Herleitungsaufwand low, max. 4096 Token, 4 parallele Anfragen), synthetische Daten, `scripts/abnahme.py`. Der User hat den Vollauf nach rund 2 h bei 875 von höchstens 1864 Aufrufen abgebrochen, weil qwen zu langsam war.
    - Pilot mit Hochrechnung (124 Aufrufe in 17 min) und Anhalten (nach 16 Aufrufen) bestanden.
    - gpt-oss-120b vollständig: 610 Aufrufe, alle gültig, 200 von 200 Einheiten mit Ergebnis, 4 repariert, 6 Konfidenzen normalisiert. Median 3,1 s je Aufruf, P95 24 s.
    - qwen3.8-27b unvollständig: 412 Aufrufe, 387 gültig, 4 ungültig, 21 Fehler (alle HTTP 429), 128 von 200 Einheiten mit Ergebnis. 24 Antworten bei 4096 Token abgeschnitten, 28 repariert. Median 15,2 s je Aufruf, P95 115 s, längster Aufruf 6 min.
    - **Befunde:** qwen3.8-27b ist über KI:connect für Vollläufe zu langsam (rund 3 Aufrufe pro Minute) und schneidet auch mit Herleitungsaufwand low Antworten ab. KI:connect lehnt mehrere gleichzeitige Anfragen an qwen mit 429 („Too many concurrent requests“) ab; daraus entstand `_Throttle` (`beb32f9`), der die Parallelität nach 429 senkt. Der abgebrochene Lauf nutzte noch die Engine ohne ihn.
    - Panel-Kennwerte (Dissens, Stabilität, LLM gegen Gold) gibt es daher nicht; die Panel-Mechanik prüfen die Tests mit Fake-Anbietern. Für eine vollständige Abnahme ein schnelleres zweites Modell wählen.
- **P4 umgesetzt:**
  - REFI-QDA .qdpx/.qdc mit stabilen GUIDs, Signalwörtern im Zweig „LLM-Evidenz“, Gold- und Review-Personen
  - Methoden-Report als HTML und DOCX mit Methodenabsatz DE/EN
  - Exporte gegen die offiziellen REFI-XSDs validiert
  - Replikationspaket mit Datenschutz-Schalter und `llmanno-rerun`
  - PPI-Prävalenz
  - `dsl`-Spalten

  Import-Abnahme in QualCoder bestanden (vom User getestet, 2026-10-07).

  Confidence-driven inference für Gate-Reviews umgesetzt (2026-10-07): „Alle Reviews gewichtet“ neben PPI++, inverse Wahrscheinlichkeitsgewichtung mit den protokollierten Ziehwahrscheinlichkeiten der aktuellen Queue und Power-Tuning (`metrics/prevalence.weighted_mean`), nur bei vollständig abgearbeiteter Queue. Per Simulation (Gate plus Stichprobe fester Größe) auf Unverzerrtheit und Abdeckung geprüft; nach dem Review: Power-Tuning erst ab 20 Zufallsfällen (auch PPI++), t-Quantil bei Clustern, dsl-Wahrscheinlichkeiten aus der aktuellen Queue. P4 ist damit vollständig.
- **Nachgerüstet nach Literatur-Scan (2026-10-06):** Prompt-Sensitivität (drei Umformulierungen und ohne Kontext, inter-prompt α, Anteile je Variante; Barrie et al. 2024, Baumann et al. 2025), GUIDE-LLM-Checkliste (Feuerriegel et al. 2026), Konfidenz-Prüfung (AURC, isotone Rekalibrierung, Panel-Signal, Stufen der Läufe), PPI++ mit cluster-robustem Intervall, Test auf differentielle Messfehler, Geltungsbereich im Methodenabsatz, Modellwechsel-Warnung, Herleitungsaufwand, Temperatur „nicht senden“.
- **Bestandsaufnahme 2026-10-06:** 45 offene Punkte, priorisiert in [ROADMAP.md](ROADMAP.md) (Abschnitt 5: Arbeitsplan).
- **P5 begonnen:** englische Oberfläche, [Anleitung](ANLEITUNG.md), Windows-Build (`packaging/llmanno.spec`). `CITATION.cff` liegt bei (`286ead5`; ORCID, Repository-URL und DOI folgen bei der Veröffentlichung). Offen: Name, Zenodo, Installation auf einem fremden Rechner. Lizenz: AGPL-3.0 oder später (2026-10-07).

| Phase | Inhalt | Abnahmekriterium |
|---|---|---|
| **P0 Fundament** | Repo-Gerüst (uv, ruff, pytest), Projektmodell, SQLite-Store, Hauptfenster mit leeren Tabs, i18n-Grundlage | Projekt anlegen, speichern und öffnen. Tests laufen. |
| **P1 Kodier-Kern** (ein Modell) | XLSX-Import + Mapping; Codebuch + Bestätigungstabelle; drei Einheiten-Modi; Prompt-Builder + Vorschau; Ollama- und OpenAI-kompatibler Anbieter mit Registry, Probe, GWDG-Preset, Keyring und API-Bestätigung; Schema + Validierung + Repair; Evidenzprüfung; N Läufe, adaptives N, beide Konfidenzen; Resume; Pilot/ETA; Ergebnisansicht; XLSX-Export | 200 Einheiten mit lokalem 4B-Modell laufen durch. Abbruch und Fortsetzen verlieren nichts. 100 % der Antworten sind valide oder als Fehler markiert. num_ctx-Kürzung wird erkannt. |
| **P2 Evaluation** | Gold-Spalten + Code-Mapping; alle Kennwerte A bis F + Bootstrap; Mensch-Mensch-Baseline; Plots; SPSS/Stata/R-Export | Alle Metriken bestehen die Referenzwert-Tests (Abschnitt 11). |
| **P3 Panel & Validierung** | Panel + Aggregation + Inter-Modell-α; Dev/Test, Freeze, Instrument-Hash, Präregistrierung; Review-Queue (blind/assistiert, Zufallsstichprobe); Kalibrierung; Alt-Test; Subgruppen | Ein Panel aus 1 lokalen + 1 GWDG-Modell läuft durch. Test bleibt vor dem Freeze gesperrt. Blinde Reviews landen als Gold-Schicht. |
| **P4 Export & Bericht** | REFI-QDA .qdpx/.qdc; Report HTML/DOCX + Methodenabsatz; Replikationspaket; PPI-Prävalenz; `dsl`-Export; Headless-Rerun | .qdpx importiert in MAXQDA, ATLAS.ti und QualCoder mit korrekten Spans. Rerun aus dem Paket reproduziert mit gleichem Digest und Seed oder dokumentiert die Abweichungen. |
| **P5 Publikationsreife** | Englische Oberfläche, PyInstaller-Build, Nutzerdoku, Beispielprojekt, Lizenz, `CITATION.cff`, Zenodo-DOI | Installation auf einem fremden Windows-Rechner ohne Python |
| **v2 (Backlog)** | Stand 2026-10-07: hierarchische Codes (zwei Ebenen als Code-Gruppen, Roadmap 10); Dawid-Skene als Gegenprobe in der Evaluation, nicht als Aggregation (Ein-Münz-Variante, ab drei Modellen; LLM-Fehler sind korreliert) ✓; Gate-Schwellen auf der rekalibrierten Skala ✓; Label-Studio-Export ✓ (INCEpTION nicht); Stichprobenrechner für Gold-Größe ✓; Shaffers ρ ✓ (gegen rhoR 1.3.1 geprüft). Beispiele aus Dev-Daten ✓ (User 2026-10-08: abschaltbar, standardmäßig aus, Beispiel-Einheiten aus allen Auswertungen ausgeschlossen) | – |

---

## 11. Teststrategie

- **Kern-Unit-Tests** mit einem `MockProvider`, der skriptbare Antworten liefert: valide, kaputtes JSON, falsche Codes, halluzinierte Signalwörter, Timeouts, 429.
- **Referenzwert-Tests der Metriken:**
  - Krippendorffs Beispieldaten (Krippendorff 2011)
  - Gwets Beispiele (AC1, Fleiss)
  - Vergleich mit R `irr` / `irrCAC` / `krippendorffsalpha` auf festen Datensätzen, die als Golden Files mit erwarteten Werten im Repo liegen
- **Golden Files** für den REFI-QDA-Export + XSD-Validierung. Dazu ein manueller Import-Check pro Release in MAXQDA, ATLAS.ti und QualCoder.
- **GUI-Smoke-Tests** mit `pytest-qt`
- **End-to-End** mit dem Beispielprojekt (synthetische Daten, kleines Codebuch), lokal gegen ein kleines Ollama-Modell

---

## 12. Risiken und methodische Grenzen

| Risiko | Gegenmaßnahme |
|---|---|
| Begründungen sind nachträgliche Plausibilisierungen und geben die tatsächlichen Gründe nicht zuverlässig wieder (Turpin et al. 2023). | als „LLM-Plausibilisierung“ kennzeichnen. Signalwörter sind prüfbar, Begründungen nicht. |
| Konsens zwischen Modellen ist keine Validität (Stolwijk et al. 2025). | Panel-α wird nie als Gütemaß berichtet, nur als Unsicherheitssignal. Validität nur gegen menschliches Gold. |
| Freitext-Codebuch: keine Prüfung pro Code, Positionseffekte im Codebuch nicht randomisierbar | Code-Liste wird permutiert. Hinweis auf strukturierte Ankerbeispiele (Halterman & Keith 2025). |
| Kleine lokale Modelle scheitern eher an impliziten bzw. kontextlastigen Codes. | Panel mit großen GWDG-Modellen, Kennwerte pro Code |
| κ-Paradox bei schiefer Verteilung | AC1, PABAK und Prävalenz-Index daneben berichten |
| kleine Test-Sets ergeben breite KIs | KIs immer zeigen, Warnung bei geringer Stützung, Stichprobenrechner im Tab Splits & Freeze |
| Kontamination: öffentliche Datensätze stecken evtl. im Training (Ludwig et al. 2024). | Hinweis im Report, eigene Daten bevorzugen |
| API-Drift: Modelle hinter einem Namen ändern sich. | zurückgegebene Version protokollieren, Warnung bei Wechsel |
| T = 0 bzw. Seed ist nicht vollständig deterministisch (Atil et al. 2024). | Test-Retest-α berichten, Reproduzierbarkeit nur „mit gleichem Digest, gleicher Version und Hardware“ zusagen |
| Ollama kürzt zu lange Prompts still. | num_ctx explizit, Abgleich mit `prompt_eval_count` |
| REFI-QDA-Offsets, lückenhafter NVivo-Import | Testfälle mit Sonderzeichen, NVivo nur nach bestem Bemühen |
| Datenschutz bei API-Nutzung | lokal als Default, Bestätigungsdialog, Datenschutz-Stufe pro Anbieter, Rohtexte im Replikationspaket abschaltbar |

---

## 13. Offene Punkte

1. **Arbeitstitel** des Tools (Paketname `llmanno` ist ein Platzhalter)
2. ~~**Lizenz** für die Veröffentlichung~~: entschieden 2026-10-07, AGPL-3.0 oder später (passt zu pyreadr, das ebenfalls AGPL ist)
3. **Abnahmeziele im QDA-Bereich:** welche Versionen von MAXQDA und ATLAS.ti stehen zum Testen zur Verfügung?
4. **Default-Panel:** welche GWDG-Modelle? Die Liste ändert sich, deshalb nach dem ersten Verbindungstest festlegen. Bisher gibt es kein festes Panel; die Modelle kommen aus dem Verbindungstest. Abnahme über KI:connect (2026-10-07): gpt-oss-120b stabil, qwen3.8-27b für Vollläufe zu langsam.
5. ~~**Ordinale Codes**~~: umgesetzt als Skalenniveau „ordinal“ im Codebuch-Tab (Rang je Code, ordinales α, quadratisch gewichtetes κ).
6. ~~**Gruppen**~~: entschieden 2026-10-07 (Roadmap 15): gruppenweise, sobald eine Gruppen-Spalte gesetzt ist; Schalter „Einheiten einer Gruppe als unabhängig behandeln“ im Split-Tab.
7. ~~**Rohtexte im Replikationspaket**~~: entschieden 2026-10-07, standardmäßig aus, per Opt-in.

---

## 14. Literatur (Kernauswahl)

**Kodieren mit LLMs**

- Barrie, C., Palaiologou, E., & Törnberg, P. (2024). Prompt stability scoring for text annotation with large language models. [arXiv:2407.02039](https://arxiv.org/abs/2407.02039)
- Baumann, J. et al. (2025). Large language model hacking: Quantifying the hidden risks of using LLMs for text annotation. [arXiv:2509.08825](https://arxiv.org/abs/2509.08825)
- Feuerriegel, S. et al. (2026). A reporting checklist for large language models in behavioural science (GUIDE-LLM). *Nature Human Behaviour*. [doi:10.1038/s41562-026-02492-7](https://doi.org/10.1038/s41562-026-02492-7)
- Jowsey, T., Braun, V., Clarke, V. u. a. (2025). Offener Brief zu GenAI in reflexiver qualitativer Forschung. [SSRN 5676462](https://papers.ssrn.com/abstract=5676462)
- Dunivin, Z. O. (2024/2025). Scalable qualitative coding with LLMs: Chain-of-thought reasoning matches human performance in some hermeneutic tasks. *EPJ Data Science*. [arXiv:2401.15170](https://arxiv.org/abs/2401.15170)
- Gilardi, F., Alizadeh, M., & Kubli, M. (2023). ChatGPT outperforms crowd workers for text-annotation tasks. *PNAS*. [arXiv:2303.15056](https://arxiv.org/abs/2303.15056)
- Halterman, A., & Keith, K. A. (2025). Codebook LLMs: Evaluating LLMs as measurement tools for political science concepts. *Political Analysis*. [arXiv:2407.10747](https://arxiv.org/abs/2407.10747)
- Pangakis, N., Wolken, S., & Fasching, N. (2023). Automated annotation with generative AI requires validation. [arXiv:2306.00176](https://arxiv.org/abs/2306.00176)
- Reiss, M. V. (2023). Testing the reliability of ChatGPT for text annotation and classification: A cautionary remark. [arXiv:2304.11085](https://arxiv.org/abs/2304.11085)
- Xiao, Z. et al. (2023). Supporting qualitative analysis with LLMs: Combining codebook with GPT-3 for deductive coding. *IUI Companion*. [arXiv:2304.10548](https://arxiv.org/abs/2304.10548)
- Ziems, C. et al. (2024). Can large language models transform computational social science? *Computational Linguistics*. [ACL Anthology](https://aclanthology.org/2024.cl-1.8/)
- Marston et al. (2026). Benchmark mit 46 LLMs für deduktives Kodieren. [arXiv:2606.26541](https://arxiv.org/abs/2606.26541)
- Cheng, Z., Kasai, J., & Yu, T. (2023). Batch prompting: Efficient inference with large language model APIs. *EMNLP Industry Track*. [arXiv:2301.08721](https://arxiv.org/abs/2301.08721)
- Chen, L., Zaharia, M., & Zou, J. (2024). FrugalGPT: How to use large language models while reducing cost and improving performance. *TMLR*. [arXiv:2305.05176](https://arxiv.org/abs/2305.05176)

**Validität, Inferenz, Berichtsstandards**

- Angelopoulos, A. N. et al. (2023). Prediction-powered inference. *Science*. [arXiv:2301.09633](https://arxiv.org/abs/2301.09633)
- Barrie, C., Palmer, A., & Spirling, A. (2024). Replication for language models. [GitHub](https://github.com/ArthurSpirling/LargeLanguageReplication)
- Baumann, J. et al. (2025). Large language model hacking: Quantifying the hidden risks of using LLMs for text annotation. [arXiv:2509.08825](https://arxiv.org/abs/2509.08825)
- Calderon, N., Reichart, R., & Dror, R. (2025). The alternative annotator test for LLM-as-a-judge. *ACL*. [ACL Anthology](https://aclanthology.org/2025.acl-long.782/)
- Egami, N. et al. (2023). Using imperfect surrogates for downstream inference: Design-based supervised learning. *NeurIPS*. [arXiv:2306.04746](https://arxiv.org/abs/2306.04746)
- Gligorić, K. et al. (2025). Can unconfident LLM annotations be used for confident conclusions? *NAACL*. [arXiv:2408.15204](https://arxiv.org/abs/2408.15204)
- Ludwig, J., Mullainathan, S., & Rambachan, A. (2024). Large language models: An applied econometric framework. [arXiv:2412.07031](https://arxiv.org/abs/2412.07031)
- Stolwijk, S. B., Boukes, M., & Trilling, D. (2025). Are generative AI text annotations systematically biased? [arXiv:2512.08404](https://arxiv.org/abs/2512.08404)
- Törnberg, P. (2024). Best practices for text annotation with large language models. *Sociologica*. [Link](https://sociologica.unibo.it/article/view/19461)
- TRIPOD-LLM (2025). Reporting guideline for studies using LLMs. [PDF](https://www.tripod-statement.org/wp-content/uploads/2025/01/TRIPOD-LLM-Article.pdf)
- Zrnic, T., & Candès, E. J. (2024). Active statistical inference. *ICML*.

**Prompt- und Ausgabeeffekte, Unsicherheit**

- Aggarwal, P. et al. (2023). Let's sample step by step: Adaptive-consistency for efficient reasoning with LLMs. *EMNLP*. [arXiv:2305.11860](https://arxiv.org/abs/2305.11860)
- Atil, B. et al. (2024). Non-determinism of „deterministic“ LLM settings. [arXiv:2408.04667](https://arxiv.org/abs/2408.04667)
- Guo, C. et al. (2017). On calibration of modern neural networks. *ICML*. [arXiv:1706.04599](https://arxiv.org/abs/1706.04599)
- Li, Y. et al. (2024). Escape sky-high cost: Early-stopping self-consistency. *ICLR*. [arXiv:2401.10480](https://arxiv.org/abs/2401.10480)
- Pezeshkpour, P., & Hruschka, E. (2024). LLMs' sensitivity to the order of options in multiple-choice questions. *NAACL Findings*. [arXiv:2308.11483](https://arxiv.org/abs/2308.11483)
- Sclar, M. et al. (2024). Quantifying language models' sensitivity to spurious features in prompt design. *ICLR*. [arXiv:2310.11324](https://arxiv.org/abs/2310.11324)
- Tam, Z. R. et al. (2024). Let me speak freely? Format restrictions and LLM performance. *EMNLP Industry*. [ACL Anthology](https://aclanthology.org/2024.emnlp-industry.91/)
- Tian, K. et al. (2023). Just ask for calibration. *EMNLP*. [ACL Anthology](https://aclanthology.org/2023.emnlp-main.330/)
- Turpin, M. et al. (2023). Language models don't always say what they think. *NeurIPS*. [arXiv:2305.04388](https://arxiv.org/abs/2305.04388)
- Wang, X. et al. (2023). Self-consistency improves chain of thought reasoning. *ICLR*. [arXiv:2203.11171](https://arxiv.org/abs/2203.11171)
- Wang, X. et al. (2024). Human-LLM collaborative annotation through effective verification of LLM labels. *CHI*.
- Xiong, M. et al. (2024). Can LLMs express their uncertainty? *ICLR*. [arXiv:2306.13063](https://arxiv.org/abs/2306.13063)
- Zheng, C. et al. (2024). Large language models are not robust multiple choice selectors. *ICLR*. [arXiv:2309.03882](https://arxiv.org/abs/2309.03882)

**Reliabilität und Klassifikationsgüte**

- Byrt, T., Bishop, J., & Carlin, J. B. (1993). Bias, prevalence and kappa. *J. Clin. Epidemiol.*
- Chicco, D., & Jurman, G. (2020). The advantages of the MCC over F1 score and accuracy. *BMC Genomics*.
- Cohen, J. (1960; 1968). Kappa; weighted kappa.
- Dawid, A. P., & Skene, A. M. (1979). Maximum likelihood estimation of observer error-rates using the EM algorithm. *Applied Statistics*, 28(1), 20–28.
- Fleiss, J. L. (1971). Measuring nominal scale agreement among many raters. *Psychological Bulletin*.
- Fleiss, J. L., Cohen, J., & Everitt, B. S. (1969). Large sample standard errors of kappa and weighted kappa. *Psychological Bulletin*, 72(5), 323–327 (Stichprobenrechner).
- Gwet, K. L. (2008). Computing inter-rater reliability and its variance in the presence of high agreement. *BJMSP*.
- Hayes, A. F., & Krippendorff, K. (2007). Answering the call for a standard reliability measure for coding data. *Communication Methods and Measures*.
- Krippendorff, K. (2011). [Computing Krippendorff's alpha-reliability](https://www.asc.upenn.edu/sites/default/files/2021-03/Computing%20Krippendorff's%20Alpha-Reliability.pdf); (2018). *Content Analysis* (4. Aufl.).
- Landis, J. R., & Koch, G. G. (1977). The measurement of observer agreement for categorical data. *Biometrics*.
- Passonneau, R. (2006). Measuring agreement on set-valued items (MASI). *LREC*.
- Shaffer, D. W. (2017). *Quantitative Ethnography*. Cathcart Press; R-Paket [rhoR](https://cran.r-project.org/package=rhoR) (Eagan, Rogers, Pozen, Marquart & Shaffer), Vorlage für ρ.

**Qualitative Inhaltsanalyse**

- Kuckartz, U., & Rädiker, S. (2022). *Qualitative Inhaltsanalyse. Methoden, Praxis, Computerunterstützung* (5. Aufl.).
- Mayring, P. (2022). *Qualitative Inhaltsanalyse. Grundlagen und Techniken* (13. Aufl.).

**Standards:** REFI-QDA Project Exchange Standard, [qdasoftware.org](https://www.qdasoftware.org/)
