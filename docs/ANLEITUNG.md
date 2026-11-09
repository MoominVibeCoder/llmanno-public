# Anleitung

Diese Anleitung beschreibt, wie das Programm eine deduktive Kodierung mit LLMs so durchführt, dass sie sich berichten und prüfen lässt. Der technische Plan steht in [PLAN.md](PLAN.md).

## Wofür das Programm gedacht ist

- **Deduktives Kodieren** von Texteinheiten (Gesprächszüge, Antworten, Absätze) mit einem vorhandenen Codebuch
- **Validierung** gegen menschliche Kodierungen mit den in der empirischen Forschung üblichen Kennwerten
- **Übergabe** an QDA-Programme und Statistiksoftware

Nicht gedacht ist es für induktive Kategorienbildung oder als Ersatz für menschliches Urteil ohne Validierung. LLM-Kodierungen sind erst nach einer Prüfung gegen menschliches Gold belastbar.

Für reflexive oder interpretative Verfahren (etwa reflexive thematische Analyse) passt es nicht: Dort ist das Kodieren selbst der Erkenntnisprozess der Forschenden, und ein offener Brief von Jowsey, Braun, Clarke u. a. (2025) hält GenAI dafür für ungeeignet. Das Codebuch sollte von Menschen entwickelt sein, bevor das LLM kodiert (Dunivin 2025). Der Methodenabsatz des Reports nennt den Geltungsbereich deshalb ausdrücklich.

## Installation und Start

**Windows-Programm, ohne Python:** Das ZIP entpacken, etwa nach `C:\Programme\llmanno` oder in den eigenen Benutzerordner, und `llmanno.exe` starten. Die Programme sind nicht signiert; Windows SmartScreen fragt deshalb beim ersten Start nach („Weitere Informationen“ → „Trotzdem ausführen“). Im selben Ordner liegen `llmanno-rerun.exe` für den Rerun in der Konsole, der Ordner `Beispiel` mit dem Beispielprojekt und die Lizenzen (`LICENSE.txt`, `Lizenzen`). Einstellungen (Sprache, zuletzt geöffnete Projekte) speichert Windows je Benutzer; Fehlerprotokolle landen in `%LOCALAPPDATA%\llmanno\LLM-Annotation\fehler.log`.

**Ausprobieren ohne eigene Daten:** **Demo öffnen** (Projekt-Tab oder Menü Datei) legt das Beispielprojekt an (200 synthetische Gesprächszüge aus Mathestunden, Codebuch mit vier Codes, zwei Gold-Kodierungen) und füllt es mit simulierten Antworten zweier „Demo-Simulator“-Modelle. Das sind keine LLM-Ergebnisse; sie entstehen aus der Kodierung „Coder A“ mit eingestreuten Fehlern, laufen aber durch dieselbe Verarbeitung wie echte Antworten. So lassen sich Ergebnisse, Review, Evaluation, Split und Exporte ansehen, ohne Daten hochzuladen oder ein Modell einzurichten. Die Demo liegt im Benutzerprofil (`%LOCALAPPDATA%\llmanno\LLM-Annotation\Demo`); eine vorhandene öffnet das Programm wieder oder legt auf Wunsch eine neue an. Für echte Läufe im Tab Modelle ein Modell ins Panel übernehmen.

**Erste Schritte im Programm:** **Datei → Neues Projekt…** (oder der Knopf auf der Startseite) startet einen Assistenten, der Ordner, Daten, Codebuch und Modell abfragt; **Neues leeres Projekt…** wählt nur den Ordner. Über den Tabs zeigt die Leiste **Nächster Schritt**, was erledigt ist und was als Nächstes kommt, mit einem Knopf zum passenden Tab (ausblendbar über **Ansicht → Nächsten Schritt zeigen**). Jedes Bedienelement erklärt sich im Tooltip; **F1** (Hilfe → Anleitung zu diesem Tab) öffnet diese Anleitung beim passenden Abschnitt, **Hilfe → Glossar** die Begriffe. Profi-Einstellungen, deren Vorgaben für den Einstieg passen, liegen in eingeklappten Bereichen **Erweitert**; **Ansicht → Erweiterte Einstellungen zeigen** öffnet alle.

**Aus dem Quelltext** (mit [uv](https://docs.astral.sh/uv/)):

```bash
uv sync
```

```bash
uv run llmanno
```

Lokale Modelle laufen über [Ollama](https://ollama.com) (`ollama serve`). Auf Rechnern mit wenig Grafikspeicher (um 4 GB) passen Textmodelle bis etwa 4 Milliarden Parameter; größere laufen teilweise auf der CPU und werden sehr langsam. Für größere Modelle eignen sich akademische APIs wie GWDG/KISSKI oder KI:connect.NRW.

### Akademische APIs: GWDG/KISSKI und KI:connect.NRW

Beide sind im Tab Modelle voreingestellt (Datenschutz-Stufe „akademisch / EU“). Den API-Key erzeugt man im Portal des Anbieters über die eigene Hochschule und speichert ihn mit **Im Schlüsselbund speichern**; **Verbindung testen** listet dann die Modelle, **Fähigkeiten prüfen** (unter Erweitert) ermittelt, ob der Anbieter ein festes Antwortformat (Structured Outputs) und Wiederholbarkeit (Seeds) unterstützt. Ein eingetippter, noch nicht gespeicherter Key wird beim Testen gespeichert. Die Modellnamen stehen so in der Liste, wie der Anbieter sie führt, bei KI:connect etwa „OpenAI GPT OSS 120B“ oder „qwen3.8-27b“.

- **Temperatur:** Manche Modelle ignorieren sie beim Anbieter (bei KI:connect gpt-oss und GPT-5). Dann im Panel „nicht senden“ wählen; der Report nennt die Temperatur dann ehrlich als Vorgabe des Anbieters.
- **Reasoning-Modelle** (gpt-oss, qwen3, GPT-5) brauchen ein großes `max_tokens` (etwa 4096), sonst schneidet das Denken die Antwort ab und der Aufruf gilt als abgeschnitten. Beim Übernehmen ins Panel setzt das Programm für erkennbar denkende Modelle 4096.
- **Rate-Limits:** Im Tab Modelle lässt sich je Anbieter eine Höchstzahl Anfragen pro Minute setzen. Meldet der Anbieter trotzdem „zu viele Anfragen“ (HTTP 429), wartet das Programm (2, 5, 15, 30 s oder so lange, wie der Anbieter per Retry-After verlangt, bei GWDG per ratelimit-reset) und versucht es erneut. Verlangt er mehr als 5 Minuten Pause, ist meist das Kontingent erschöpft: Der Lauf hält an und lässt sich später fortsetzen, ohne dass etwas verloren geht.
- **Netzausfall:** Nach fünf Verbindungsfehlern in Folge wartet der Lauf (1, 2, 5, 10 min) statt abzubrechen, insgesamt bis zu 30 Minuten; der Lauf-Tab zeigt das an.
- **Verbrauch und Kontingent:** Nach jedem Lauf nennt der Lauf-Tab die Token laut Anbieter (Eingabe, Ausgabe, davon Reasoning, aus dem Cache, soweit der Anbieter sie meldet), die mittlere Zahl der Aufrufe je Einheit und Modell und das restliche Kontingent, wenn der Anbieter es mitschickt (GWDG: Anfragen pro Minute, Stunde, Tag und Monat). Vor dem nächsten Lauf schätzt der Zustimmungsdialog Aufrufe und Token als Spanne (je nach adaptivem Stopp; beim Fortsetzen ohne die schon fertigen Aufrufe), die Ausgabe nach den bisherigen Antworten der Modelle, und warnt, wenn schon der günstigste Fall das zuletzt gemeldete Tages- oder Monatskontingent übersteigt. Auch ein abgebrochener Lauf, etwa bei erschöpftem Kontingent, hinterlässt Verbrauch und Kontingent im Protokoll.
- **Parallele Anfragen:** Große Projekte brauchen nacheinander lange (ein Dreier-Panel mit 20.000 Einheiten mehrere Tage). Im Tab Modelle lässt sich je Anbieter einstellen, wie viele Anfragen gleichzeitig laufen. Mehr als 1 nur, wenn der Anbieter es zulässt (bei Ollama die Server-Einstellung `OLLAMA_NUM_PARALLEL`). Prompts, Seeds und die Lauf-Identität bleiben gleich; manche Server antworten unter paralleler Last trotzdem leicht anders, deshalb nennt der Report den Wert. Meldet der Anbieter „zu viele gleichzeitige Anfragen“ (HTTP 429), schickt der Lauf weniger gleichzeitig (protokolliert); KI:connect.NRW etwa vertrug in der Abnahme keine vier gleichzeitigen Anfragen an qwen3.8-27b.

## Arbeitsablauf

Die Tabs folgen dem Ablauf von links nach rechts.

Eine neue Erhebungswelle mit demselben, schon validierten Instrument: **Datei → Neues Projekt aus diesem Instrument …**. Codebuch, Code-Liste, Prompt- und Kodierparameter, Panel, Review- und Gold-Regeln werden übernommen, Split, Freeze, Läufe und Reviews nicht. Der Report nennt das Herkunftsprojekt und ob das Instrument seither unverändert ist.

**Mehrere Codesysteme** (etwa Reference Move als eigene Dimension neben dem Hauptcodebuch): oben rechts **+ Codesystem…**, dann Name, Codebuch, Gold-Spalten, Kodiermodus und k des neuen Codesystems wählen.

- Jedes Codesystem kodiert dieselben Einheiten mit seinem eigenen Codebuch in eigenen Aufrufen; die Messung ist also dieselbe wie in einem Projekt mit nur diesem Codebuch.
- Die Auswahl **Codesystem** daneben bestimmt, was die Tabs Codebuch, Prompt & Einheiten, Splits & Freeze, Ergebnisse, Review, Evaluation und Export zeigen. Code-Liste, Modus, Prompt-Parameter, Gold, Ergebnisse, Review-Queue, Kennwerte, Freeze, Report und Replikationspaket hat jedes Codesystem für sich. **Codesystem…** ändert Gold-Spalten, Modus und k des gewählten (neue Gold-Spalten vorher im Daten-Tab des Hauptprojekts nachtragen).
- Gemeinsam sind Daten, Modelle, Prompt-Sprache, Analyseeinheit samt Kontext und Zielspalte sowie die Namensliste. Sie werden im Hauptprojekt eingestellt und beim Wechsel und vor jedem Lauf übernommen. Ist ein weiteres Codesystem eingefroren, bricht eine Änderung daran auch dessen Freeze; die Statuszeile nennt eingefrorene Codesysteme. Neue Texte lassen sich nicht mehr importieren, sobald irgendein Codesystem Läufe hat.
- Der **Lauf** kodiert alle Codesysteme nacheinander, mit einer Zustimmung und einem Budget für alle. Ein unfertiges Codesystem (etwa ohne aktive Codes) wird übersprungen und im Protokoll genannt; ein gebrochener Freeze stoppt nur sein Codesystem. Pilot, „nur Dev“ und Prompt-Sensitivität gelten je Codesystem; eingefrorene weitere Codesysteme kodieren auch mit „nur Dev“ alle Einheiten.
- Der Split eines weiteren Codesystems übernimmt die Aufteilung des Hauptprojekts, wenn dieselben Einheiten Gold haben; sonst teilt er nach seinem eigenen Gold und den Split-Regeln, die beim Anlegen aus dem Hauptprojekt kamen.
- XLSX und REFI-QDA können alle Codesysteme enthalten (Schalter „Alle Codesysteme exportieren“ im Tab Export): die XLSX-Datei mit eigenen Blättern „Ergebnisse – Name“ usw. und Info-Blatt, das REFI-QDA-Projekt mit den Texten einmal und je Codesystem einem eigenen Zweig im Codesystem (Codes, Signalwörter, Notizen). Gemeinsam geht REFI-QDA nur, wenn alle Codesysteme dieselben Daten kodieren; ein Codesystem, das noch die alten Daten kodiert, bitte einzeln exportieren. Codesysteme ohne bestätigte Codes bleiben draußen; die Statuszeile nennt sie. Die Rückfrage zur Test-Einsicht vor dem Freeze zählt alle mit. Report und Replikationspaket gibt es je Codesystem.
- Weitere Codesysteme liegen als vollständige Projekte unter `codesysteme/<Name>/` im Projektordner. Ein Codesystem entfernen heißt, diesen Ordner bei geschlossenem Projekt zu löschen. **Neues Projekt aus diesem Instrument** übernimmt nur das Hauptprojekt.

### 1. Projekt

Ein Projekt in einem leeren Ordner anlegen. Im Projektordner liegen `project.json` (Konfiguration), `store.sqlite` (alle Aufrufe und das Protokoll), das Codebuch und eine Kopie der Daten. Ein geöffnetes Projekt ist gesperrt (`project.lock`), damit kein zweites Programm die Konfiguration überschreibt; nach einem Absturz bietet das Programm an, die Sperre aufzuheben. Projekte gehören auf ein lokales Laufwerk: Auf Netzlaufwerken und in Cloud-Ordnern (OneDrive, Sciebo, Dropbox …) kann die Datenbank kaputtgehen; der Projekt-Tab warnt dann. Unter **Datei → Zuletzt geöffnet** stehen die letzten acht Projekte; **Datei → Protokoll anzeigen** listet jedes Ereignis des Projekts (Import, Läufe, Freeze, Exporte, Zustimmungen) mit Zeit und Details, filterbar. **Hilfe → Über llmanno** nennt die Version zum Zitieren. Der **Kodiermodus** heißt „Ein Code je Einheit (Single-Label)“ oder „Mehrere Codes je Einheit (Multi-Label)“; die Zahl der Codes je Einheit (k) liegt unter Erweitert. Sprache, Modus und k nach Läufen zu ändern, startet eine neue Kodierung.

### 2. Daten

XLSX- oder CSV-Datei wählen und Spalten zuordnen. Bei CSV erkennt das Programm Trennzeichen (Komma, Semikolon, Tab; aus der Kopfzeile) und Kodierung (UTF-8 oder Windows) selbst. Zahlen werden nur dann Zahlen, wenn sie dabei unverändert bleiben: „17“ und „1.5“ ja, „007“, „1.10“ oder „3,5“ bleiben Text, damit IDs und Codes nicht zusammenfallen. Hat eine Zeile mehr Felder als die Kopfzeile (meist ein ungeschütztes Trennzeichen im Text), landen die überzähligen in eigenen Spalten, und der Prüfbericht warnt.
- Liegen die Daten in **mehreren Dateien oder Blättern** (etwa ein Transkript je Stunde), führt **Mehrere zusammenführen…** sie zu einer XLSX-Datei zusammen. Bei Arbeitsmappen mit mehreren Blättern fragt das Programm, welche dazugehören. Vorn stehen dann „Quelle“ (Datei, bei mehreren Blättern mit Blatt) und „Quelle_ID“ (Quelle und Zeile); beide sind als Gruppe und ID vorgewählt. Spalten, die nicht in allen Quellen vorkommen, bleiben dort leer; der Prüfbericht nennt sie. Importiert wird die zusammengeführte Datei wie jede andere, auch für Gold-Nachträge und den Rerun. Die Dateinamen werden so zu Gruppen- und Einheiten-IDs: Enthalten sie Namen, im Replikationspaket die IDs pseudonymisieren.
- **ID** und **Text** sind Pflicht.
- **Gruppe** (etwa ein Transkript) und **Reihenfolge** braucht der Kontextmodus.
- **Gold** sind die Spalten mit menschlichen Kodierungen, eine je Person, optional dazu ein Konsens.
- Unter **Erweitert:** Reihenfolge, Metadaten, Konsens, Analyse-Variablen und „Gold aus QDA-Projekt“; der Titel nennt, was dort belegt ist.
- **Analyse-Variablen** (etwa Geschlecht oder Schulform) gehen nie in den Prompt, stehen aber in der Evaluation als Subgruppen und im Messfehler-Test zur Verfügung; ins Replikationspaket kommen sie nur mit Rohtexten. Sie lassen sich auch nach Kodierläufen ändern.

Nach dem Import sind die Daten festgeschrieben, sobald kodiert wurde. Gold lässt sich auch danach nachtragen oder korrigieren, etwa wenn eine zweite Person dazukommt: dieselbe Datei mit den neuen Gold-Spalten wählen, ankreuzen, **Gold aktualisieren**. Die Texte müssen gleich bleiben. Gold gehört nicht zum Instrument; Lauf-Identitäten und Freeze bleiben, die Änderung steht im Protokoll, und die bisherige Datenkopie bleibt als Sicherung im Projektordner. Ändert sich die Referenz auf dem Test-Set nach einer Test-Auswertung (Gold nachgetragen, Zuordnung oder Trennzeichen geändert), nennt die Evaluation das.

### 3. Codebuch

Textdatei laden. Sie geht unverändert in den Prompt. Das Programm schlägt die Codes vor; die **Code-Liste** prüfen, denn nur diese Codes vergibt das Modell und nur sie werden ausgewertet. Änderungen gelten sofort, ein eigenes Bestätigen gibt es nicht. Die Restkategorie „kein Code passt“ ist zuschaltbar. Das Muster für „Codes vorschlagen“, Skalenniveau sowie Kürzel und Beschreibung der Restkategorie liegen unter Erweitert.
- **Code-Gruppen:** In der Spalte „Gruppe“ lassen sich Codes einer Oberkategorie zuordnen, etwa acht Transaktivitäts-Codes vier Diskursmodi. Die Evaluation rechnet dann zusätzlich auf Ebene der Gruppen (Übereinstimmung, κ, α, AC1, Mensch-Mensch-α), Report und Kennwerte-XLSX nennen beide Ebenen, und der REFI-QDA-Export legt die Gruppen als Obercodes an. In den Prompt gehen die Gruppen nicht; sie ändern keine Lauf-Identität. Sie gehören aber zum Analyseplan: Wer nach dem Freeze umgruppiert, bricht ihn.
- **Gold-Werte zuordnen:** Stehen in den Gold-Spalten Werte, die keinem Code entsprechen (etwa „1“ statt „K1“), hier zuordnen oder ignorieren, und zwar vor dem Split: Sonst fehlen diese Einheiten in Dev und Test. Bei Multi-Label steht hier auch das Trennzeichen; „Aus Gold-Spalten ergänzen“ nutzt es. Der Tab Splits & Freeze warnt vor solchen Lücken.
- **Unstimmigkeiten prüfen:** Der Knopf zeigt die Code-Paare, an denen die Modelle vom Gold oder voneinander abweichen, die häufigsten zuerst, mit den Fällen (Text, Gold, Code und Begründung je Modell). Sobald es eine Aufteilung gibt, nur aus dem Übungsteil (Dev); Prüfteil (Test) und Beispiel-Einheiten im Prompt bleiben immer außen vor. **Abgrenzung vorschlagen (LLM)** schickt Codebuch und bis zu 12 Fälle eines Paars an ein Modell des Panels, bei externen Anbietern erst nach Zustimmung und mit ersetzten Namen wie beim Kodieren. Es schlägt eine schärfere Regel, Ankerbeispiele und fragliche Gold-Fälle vor. Der Vorschlag ändert nichts von selbst: Das Codebuch bearbeitet man selbst und lädt es neu, danach den Übungsteil neu kodieren und auswerten. Vorschläge stehen im Projektprotokoll, jede geladene Fassung des Codebuchs ohnehin im Bericht. **Als Arbeitsblatt speichern (XLSX)** legt Paare, letzte Vorschläge und Fälle für die Besprechung im Team ab. Erreicht ein Modell am Übungsteil 0,81, ist das noch kein Beleg: Es zählt erst die Auswertung des Prüfteils nach dem Freeze.

### 4. Modelle

Anbieter einrichten (Ollama, GWDG/KISSKI, KI:connect.NRW oder ein eigener OpenAI-kompatibler Anbieter) und Modelle ins Panel übernehmen. Kommt ein kleines lokales Modell (unter 10 Mrd. Parameter) ins Panel, schaltet das Programm die **Nachfrage (Repair), wenn Signalwörter nicht im Text stehen**, aus: Er kostet dort einen zweiten Aufruf und hilft selten. Im Tab Prompt & Einheiten lässt er sich wieder einschalten; dort steht dann ein Hinweis. API-Schlüssel liegen im Schlüsselbund des Betriebssystems, nie im Projekt. **Key entfernen** löscht ihn dort wieder; er gilt je Basis-URL, also auch für andere Projekte mit demselben Anbieter. Bei den voreingestellten Anbietern liegen Art, Basis-URL, Authentifizierung, Rate-Limit, Zeitlimit, parallele Anfragen und Fähigkeiten unter Erweitert, im Panel ebenso die Spalten Temperatur, Thinking und Reasoning-Aufwand. Verbindungsfehler nennen die übliche Ursache (Ollama nicht gestartet, Key fehlt oder ungültig, Modell unbekannt, Kontingent erschöpft).

### 5. Prompt & Einheiten

- **Analyseeinheit** wählen: Zeile isoliert, Zeile mit Kontextfenster oder Gruppe holistisch.
- **Kodieren nur, wo diese Spalte ja ist:** Zeilen mit „nein“ (leer, 0, nein, no) stehen nur als Kontext im Prompt, etwa Züge der Lehrkraft, und werden nicht kodiert.
- **Durchläufe pro Einheit (N)** festlegen: wie oft jedes Modell dieselbe Einheit kodiert.
- Unter **Erweitert:** adaptiver Stopp, Startwert (Seed), Mischen der Code-Reihenfolge, Signalwörter und Begründung, Nachfrage (Repair) und Beispiele aus dem Übungsteil.
- **Namen ersetzen…** (Pseudonymisierung, siehe Datenschutz): Namen in Text, Sprecher und Metadaten gehen dann als Pseudonym an das Modell.
- **Beispiele aus dem Übungsteil (Dev)** (Few-Shot, standardmäßig aus): **Beispiele auswählen** wählt je Code bis zu fünf Dev-Einheiten mit Gold (Referenz des Splits, Startwert) und stellt sie mit ihren Codes in die System-Nachricht; **Entfernen** schaltet sie ab. Das geht erst nach dem Split, Test-Einheiten kommen nie in Frage. Die Beispiel-Einheiten fehlen in allen Kennwerten, in Gate-Kalibrierung, Review-Queue und Prompt-Sensitivität, denn das Modell hat ihr Gold gesehen; Dev wird dadurch kleiner. Maßgeblich sind die Beispiele der Läufe, auf denen die Ergebnisse beruhen, und die eingestellten: Wer nach einem Lauf Beispiele entfernt oder neu auswählt, holt die gesehenen nicht zurück. XLSX- und Statistik-Export markieren sie (`beispiel_im_prompt`, `beispiel`; das R-Skript filtert sie für dsl heraus). Die gezogenen IDs und Codes gehören zum Instrument: Neu auswählen oder entfernen startet einen neuen Lauf und bricht einen Freeze. Einen neuen Split gibt es erst nach dem Entfernen, sonst könnten Beispiele ins Test-Set rutschen. Ändert sich die Analyseeinheit, entfernt das Programm die Beispiele, auch in weiteren Codesystemen; passen sie nicht mehr zu Codebuch oder Modus, meldet es das. Eine neue Erhebungswelle und ein neues Codesystem übernehmen keine; der Report der neuen Welle nennt das. Ohne Rohtexte enthält das Replikationspaket keinen Hash der System-Nachricht, weil er die Beispieltexte abdeckte. Report, Präregistrierung und GUIDE nennen nur die IDs; die Texte stehen nur im Replikationspaket mit Rohtexten. Ohne Beispiele bleibt der Prompt Zeichen für Zeichen wie vorher. Zum Abwägen: Beispiele aus den Daten können helfen, wo das Codebuch abstrakt bleibt, verankern das Modell aber an wenigen Fällen; Ankerbeispiele im Codebuch selbst sind die neutralere Wahl.
- Die **Vorschau** zeigt den vollständigen Prompt, so wie das Modell ihn bekommt: „Anweisungen und Codebuch (System-Nachricht)“ und „Text der Einheit (User-Nachricht)“. Darunter stehen die Zahl der Aufrufe insgesamt und, getrennt davon, was noch offen ist.

### 6. Splits & Freeze

Siehe „Dev/Test-Split und Freeze“ unter Methodische Empfehlungen. Dieser Schritt kommt vor dem ersten richtigen Lauf: **Aufteilung erzeugen**, dann im Tab Lauf nur den Übungsteil kodieren, die Evaluation prüfen und Codebuch oder Anweisungen schärfen, erst danach **Instrument einfrieren**. Vor dem Einfrieren fragt das Programm mit einer kurzen Checkliste nach.

### 7. Lauf

Erst den Pilot, er rechnet die Dauer hoch, dann den Vollauf. Eine Zeile oben nennt die Phase (ohne Aufteilung, Übungsphase, eingefroren) und wie viele Einheiten schon kodiert sind; mit „nur Übungsteil (Dev) kodieren“ heißt der Knopf „Übungsteil (Dev) kodieren / fortsetzen“. Anhalten verliert nichts; ein neuer Start setzt fort. Bei überlastetem Anbieter wartet der Lauf kurz und versucht es erneut; verlangt der Anbieter mehr als 5 Minuten Pause (meist ist dann das Kontingent erschöpft), bricht er mit einer Meldung ab. Fällt das Netz aus, wartet der Lauf nach fünf Fehlern in Folge zunehmend länger und bricht erst nach 30 Minuten ab; bleiben fünf Einheiten in Folge ohne gültige Antwort, bricht er ab (das Modell hält dann das Ausgabeformat nicht ein). Der Lauf-Tab zeigt jeden Fehlergrund. Ein neuer Start wiederholt die fehlgeschlagenen Aufrufe, gültige und ungültige Antworten bleiben.

### 8. Ergebnisse

LLM-Code, **Konsistenz (p_sc)** (Anteil der Durchläufe mit diesem Code), **Selbsteinschätzung (p_verbal)** (die Konfidenz, die das Modell nennt), Alternativen und **Hinweise** im Klartext (etwa „Dissens im Panel“ oder „Signalwörter nicht im Text“); die Roh-Antworten zeigt der Schalter „Roh-Antworten zeigen“. In der Detailansicht sind die Signalwörter im Text markiert. Die Spalte **Referenz** zeigt das Gold (Konsens, sonst Mehrheit, sonst die erste Gold-Spalte); die Filter „weicht von der Referenz ab“, „unsicher“ und „nur Dev“ helfen beim Schärfen des Codebuchs. Test-Gold bleibt bis zum Freeze verborgen.

### 9. Review

Unsichere Fälle und eine Zufallsstichprobe von Menschen kodieren lassen: **Prüfliste erzeugen**, dann Einheit für Einheit kodieren. Wer auf „assistiert“ umschaltet, bestätigt das vorher, denn gesehene Vorschläge sperren die blinde Review dieser Einheiten.

### 10. Evaluation

Kennwerte gegen Gold und Reviews, Methoden-Report. Oben steht ein **Kurzurteil** mit Ampel: Übereinstimmung mit der Hauptreferenz (Krippendorffs α: grün ab 0,80 bei unterer Intervallgrenze ab 0,667, gelb ab 0,667, rot darunter), Vergleich mit den Menschen untereinander (grün höchstens 0,05 unter deren α, gelb bis 0,15), schwächster Code (F1 unter den Codes mit mindestens 20 Referenzfällen: grün ab 0,70, gelb ab 0,50), genug Gold (mindestens 30 Einheiten und ein α-Intervall von höchstens 0,20 Breite) und Robustheit (Prompt-Sensitivität, Messfehler nach Subgruppen), dazu „Was jetzt“. Ohne Gold ist die Referenz die blind reviewte Zufallsstichprobe; dann heißt der nächste Schritt mehr reviewen oder berichten. Nicht berechenbare Werte (etwa α, wenn nur ein Code vorkommt) bleiben grau. Das sind Faustregeln, keine Gesetze. Die Haupttabelle zeigt zunächst die Kernkennwerte und markiert den Hauptvergleich; **Alle Kennwerte zeigen** blendet die übrigen ein, XLSX und Report enthalten immer alle.

### 11. Export

Gruppiert nach Auswerten (XLSX), QDA-Programmen (REFI-QDA), Label Studio und Veröffentlichen (Methoden-Report, Replikationspaket). Label Studio exportiert standardmäßig ohne LLM-Vorschläge. Nach dem Speichern öffnet **Ordner öffnen** den Zielordner.

## Methodische Empfehlungen

### Dev/Test-Split und Freeze

Wer Codebuch oder Prompt anhand derselben Daten verbessert, an denen er die Güte misst, überschätzt die Güte („LLM-Hacking“, Baumann et al. 2025). Deshalb:

1. Im Tab **Splits & Freeze** einen Split erzeugen (Default 25 % Dev). Mit Gruppen-Spalte (etwa Transkript) werden ganze Gruppen gezogen, auch im isolierten Modus: Im Kontextmodus sähe ein Prompt sonst Zeilen aus dem anderen Teil, und Einheiten eines Transkripts sind nicht unabhängig.
2. Im Tab **Lauf** mit „nur Übungsteil (Dev) kodieren“ kodieren, in der **Evaluation** Dev auswerten, die Verwechslungen unter Codebuch → **Unstimmigkeiten prüfen** ansehen, Codebuch und Prompt schärfen und wiederholen.
3. **Einfrieren.** Der Instrument-Hash hält Codebuch, Prompt, Modelle samt Digest, Parameter, Gate-Regeln und Split fest.
4. Die **Präregistrierung** exportieren und etwa bei OSF hinterlegen.
5. Den **Vollauf** starten und das Test-Set einmal auswerten.

**Wie viel Gold?** Der Rechner im Tab Splits & Freeze schätzt, wie viele Test-Einheiten nötig sind, damit das 95-%-Intervall für κ (LLM gegen Gold) im Mittel etwa so breit ist wie gewünscht, etwa ± 0,10 bei erwartetem κ = 0,70, und wie viele Einheiten mit Gold das samt Dev-Anteil ergibt. Im Einzelfall kann das Intervall breiter ausfallen (in Simulationen bei gut 40 % der Stichproben); wer sicher gehen will, plant enger. Grundlage ist die Großstichproben-Varianz von κ (Fleiss, Cohen & Everitt 1969) mit der Verteilung der Split-Referenz, bei Multi-Label der ganzen Code-Mengen wie beim κ in der Evaluation; ohne Gold nimmt er gleich häufige Codes an. Schiefe Verteilungen brauchen deutlich mehr Einheiten. Der Rechner nennt mindestens 30 Einheiten und so viele, dass etwa 10 außerhalb der häufigsten Kategorie liegen; darunter trägt die Näherung nicht. Mit Split zeigt er auch, welche Breite das jetzige Test-Set erwarten lässt. Die Zahl gilt für unabhängige Einheiten; hängen sie über Gruppen zusammen, braucht es mehr.

Jede Test-Auswertung wird protokolliert und im Bericht gezählt. Exporte vor dem Freeze lassen die Test-Einheiten standardmäßig weg (Schalter „Vor dem Freeze nur Dev exportieren“ im Tab Export; der Statistik-Export der Evaluation folgt der Test-Sperre). Wer sie trotzdem exportiert (XLSX, REFI-QDA, Replikationspaket), wird gefragt; der Export wird protokolliert und im Bericht genannt, denn wer daraus außerhalb des Programms Kennwerte rechnet, umgeht die Test-Sperre. Wer nach dem Freeze etwas ändert, bricht ihn; das Programm sperrt dann Läufe und zeigt es in der Statuszeile an. Ein Aufheben braucht eine Begründung, die im Protokoll steht.

### Prompt-Sensitivität

Kleine Änderungen am Prompt können Ergebnisse und Schlüsse verschieben: In 37 Annotationsaufgaben führten solche Entscheidungen bei Spitzenmodellen in rund 31 % der Fälle zu falschen Schlüssen (Baumann et al. 2025). Deshalb im Tab Lauf **„Prompt-Sensitivität prüfen“**:

- Dieselben Einheiten (Dev, ohne Split alle; bei vielen eine feste Zufallsstichprobe, Default 100) werden mit drei Umformulierungen der Anweisungen kodiert, im Kontextmodus zusätzlich ohne Kontext. Aufgabe, Regeln für Signalwörter und Konfidenz und das Ausgabeformat bleiben gleich.
- Jede Variante läuft einmal je Einheit, als eigene Lauf-Identität. Hauptergebnisse und Freeze bleiben unberührt.
- Die Evaluation zeigt Krippendorffs α über Original und Umformulierungen (inter-prompt, wie der Prompt Stability Score von Barrie et al. 2024), je Variante die Übereinstimmung mit dem Original, α gegen die Referenz und die Code-Anteile, alles auf den Einheiten, die in allen Formulierungen gültig kodiert sind. Unter α 0,8 warnt sie: Dann nur Schlüsse berichten, die in allen Formulierungen halten. „Ohne Kontext“ zählt nicht zum α, denn es ändert die Analyseeinheit, nicht den Wortlaut; es steht als eigene Zeile daneben.
- **Reasoning niedrig:** Bei Modellen, die vor der Antwort denken, läuft zusätzlich das Original mit Reasoning-Aufwand „low“. Als denkend gilt ein Modell bei einem OpenAI-kompatiblen Anbieter, wenn der Aufwand gesetzt ist (und nicht schon niedrig) oder wenn seine bisherigen Antworten deutlich mehr Ausgabe-Token verbrauchten, als der sichtbare Text braucht; das zeigt sich spätestens im Original der Sensitivität. Die Zeile zählt nicht zum α. Die Evaluation nennt Übereinstimmung mit dem Original, α gegen die Referenz und je Variante die mittleren Ausgabe-Token und die Antwortzeit (ohne Wartezeiten durch Rate-Limits). Hält die Übereinstimmung und sinken die Token, spart „low“ im Tab Modelle Kontingent und Zeit; das ist ein neues Instrument, also vor dem Freeze umstellen. Sinken die Token kaum, ignoriert der Anbieter den Aufwand womöglich; das sagt die Evaluation dann. Lehnt der Anbieter den Parameter ab, bricht nur diese Zeile ab, die übrigen Varianten gelten.
- Die Varianten laufen mit Temperatur 0, damit α nicht Zufallsstreuung misst; legt der Anbieter die Temperatur fest, sagt das der Report. Nach einer Änderung an Codebuch, Prompt oder Panel gelten frühere Sensitivitätsläufe nicht mehr und müssen neu laufen.
- Die Sensitivität am besten vor dem Freeze auf Dev prüfen, und nicht so lange am Prompt feilen, bis eine Variante passt: Die Zahl der Lauf-Identitäten steht in der GUIDE-LLM-Checkliste.

### Läufe, Temperatur und Panel

- **Mehrere Durchläufe** (Default 5, adaptiv mit Abbruch nach 2 einstimmigen; Projekte von vor dem 2026-10-08 behalten 3) liefern die Self-Consistency-Konfidenz: den Anteil der Läufe, die denselben Code vergeben. Sie ist meist besser kalibriert als die Konfidenz, die das Modell selbst nennt. Beide werden gespeichert.
- Bei mehreren Läufen wählt die automatische Temperatur 0,7, sonst 0.
- Manche Anbieter legen die Temperatur selbst fest und ignorieren den Wert stillschweigend, bei KI:connect.NRW etwa gpt-oss-120b und die GPT-5-Modelle (siehe die Modellübersicht des Anbieters). Dann im Panel **Temperatur „nicht senden“** wählen. Das Programm sendet sie nicht, und Report und Präregistrierung nennen „nach Vorgabe des Anbieters“ statt einer Zahl, die nicht galt. Ein Einzellauf ist dann nicht mehr greedy.
- **Reasoning-Aufwand** (`reasoning_effort`) steuert bei Reasoning-Modellen (gpt-oss, GPT-5, Qwen), wie lange das Modell nachdenkt. Er ändert Ergebnisse und Laufzeit und gehört deshalb zur Lauf-Identität und in den Report. „nicht senden“ heißt: Vorgabe des Anbieters. Die Stufen nennt die Modellübersicht des Anbieters; das Feld nimmt auch eigene Werte an. Nur für OpenAI-kompatible Anbieter.
- **Modellwechsel:** Meldet der Anbieter während eines Laufs verschiedene Modellnamen, warnt die Auswertung, und der Report listet die gemeldeten Namen. Das kann auch eine Lastverteilung auf mehrere Server sein: KI:connect.NRW verteilte gpt-oss-120b im Abnahmelauf zufällig auf zwei vLLM-Server mit eigenen Namen. Das Blatt „Läufe“ im XLSX-Export nennt den Namen je Aufruf. Hinter einem API-Namen kann ein Anbieter das Modell austauschen, ohne dass sich der Name ändert; das erkennt kein Programm.
- Im **Panel** zählt jedes Modell gleich. Modelle aus verschiedenen Familien mischen. Das hilft aber nur begrenzt: Auch Modelle verschiedener Familien stimmen untereinander stärker überein als mit menschlicher Kodierung (Stolwijk et al. 2025). Übereinstimmung zwischen Modellen ist deshalb ein Unsicherheitssignal, kein Gütemaß.
- **Dawid-Skene als Gegenprobe** (Single-Label, ab drei Modellen): Die Evaluation schätzt ohne Gold die Trefferquote jedes Modells (Dawid & Skene 1979, Ein-Münz-Variante: eine Quote je Modell, Fehler gleich verteilt) und zeigt, wie viele Einheiten ein danach gewichtetes Panel anders kodieren würde und welches κ gegen die Hauptreferenz es hätte. Grundlage sind wie beim Panel die Anteile der Läufe je Modell; anders als dort zählen dazu die geschätzte Trefferquote jedes Modells und die geschätzte Häufigkeit der Codes. Das Panel selbst bleibt gleich gewichtet: Das Verfahren setzt voraus, dass die Modelle unabhängig voneinander irren, und das ist bei LLMs meist verletzt. Weichen beide Werte deutlich ab, lohnt ein Blick auf das schwächste Modell.
- Fehlt einem Modell für eine Einheit ein gültiges Ergebnis (Abbruch, ungültige Antworten), entscheiden die übrigen. Die Einheit trägt dann das Flag **Panel unvollständig** (Filter im Tab Ergebnisse), und Evaluation und Report nennen die Zahl solcher Einheiten.

### Kosten und Kontingent

Bei GWDG/KISSKI zählt das Kontingent in Anfragen (pro Minute, Stunde, Tag und Monat). Bei KI:connect.NRW legt jede Hochschule Grenzen für Anfragen, Token oder Kosten fest. Kommerzielle Anbieter rechnen je Token ab, die Ausgabe meist deutlich teurer als die Eingabe. In der KI:connect-Abnahme (gpt-oss-120b, Kontextmodus mit zwei Zeilen davor und einer danach) bestand ein Aufruf zu rund 63 % aus dem System-Prompt mit dem Codebuch, zu 23 % aus Reasoning, zu 8 % aus der sichtbaren Antwort und nur zu 6 % aus Zieltext und Kontext. Was hilft:

- **Adaptiver Stopp** (Default nach 2 einstimmigen Läufen): Eine klare Einheit braucht 2 statt 5 Aufrufe.
- **Reasoning niedrig**, wenn die Prompt-Sensitivität zeigt, dass es dieselben Codes liefert.
- Beim Schärfen **Pilot und „nur Dev“** und ein Modell statt des Panels; das Panel erst für den Lauf nach dem Freeze.
- Den Verbrauch und das Restkontingent zeigt der Lauf-Tab (siehe Akademische APIs).

Den Kontext zu kürzen spart kaum. Das Codebuch wird pro Aufruf mitgeschickt; Codebuch und Anweisungen stehen vorn und sind innerhalb eines Laufs gleich, sodass Anbieter mit Prompt-Caching (OpenAI, vLLM) sie wiederverwenden können. Bewusst nicht eingebaut: mehrere Einheiten in einer Anfrage (ändert das Instrument stark und ist für codebuchbasierte Analyse nicht geprüft), mehrere Antworten in einer Anfrage (`n`: alle Läufe hätten dieselbe Code-Reihenfolge, und GWDG und KI:connect dokumentieren den Parameter nicht), Batch-APIs (−50 %, nur bei kommerziellen Anbietern) und ein Panel als Kaskade, bei dem das zweite Modell nur unsichere Einheiten kodiert (die Kennwerte zwischen den Modellen gälten dann nur für die schwierigen Einheiten).

### Review

- Die Prüfliste (Queue) verbindet zwei Wege. Die **Auswahl unsicherer Fälle (Gate)** nimmt auf: geringe Konsistenz (Self-Consistency), Dissens im Panel, Restkategorie, nicht belegte Signalwörter, ungültige Antworten. Die **Zufallsstichprobe** zieht aus allen Einheiten mit protokollierter Wahrscheinlichkeit.
- **τ als Trefferquote:** Mit dem Schalter „τ als geschätzte Trefferquote“ (unter Erweitert) gilt τ nicht für die Self-Consistency selbst, sondern für die daraus geschätzte Trefferquote. Beim Einschalten lernt das Programm auf den Dev-Einheiten mit Gold (ohne Split auf allen, mindestens 20) eine isotone Abbildung von der Self-Consistency des unsichersten finalen Codes auf die Quote richtiger Kodierungen, gemessen an der Referenz des Splits. τ = 0,8 heißt dann: Review, wenn die geschätzte Trefferquote unter 80 % liegt. Test-Einheiten fließen nie ein. Die Abbildung gehört zum Instrument: Der Freeze hält sie fest, eine neue Erhebungswelle aus diesem Instrument übernimmt sie, ein neues Codesystem nicht (eigenes Codebuch). Zum Neulernen, etwa nach mehr Dev-Gold, den Schalter aus- und wieder einschalten. Trennt die Self-Consistency richtige und falsche Kodierungen kaum (AUROC in der Kalibrierung der Evaluation), erhalten viele Einheiten denselben Wert, und das Gate wählt alle oder keine. Der Filter „unsicher“ im Tab Ergebnisse folgt derselben Regel.
- **Blind** (Default) sieht die Person nur Text und Codebuch. Nur blinde Reviews zählen als Gold, denn sichtbare LLM-Vorschläge verzerren das Urteil. Wer eine Einheit assistiert gesehen hat, kann sie nicht mehr blind reviewen; das gilt schon, sobald der Vorschlag angezeigt wurde, auch ohne Speichern. Der assistierte Modus zeigt deshalb erst nach Eingabe des Namens Vorschläge.
- Die Prüfliste **vollständig** abarbeiten: Übersprungene Einheiten der Zufallsstichprobe machen sie nicht mehr zufällig; die Evaluation warnt dann.
- **Unverzerrt** sind nur Kennwerte gegen die Zufallsstichprobe. Das Gate wählt gezielt schwierige Fälle; Kennwerte gegen „alle Reviews“ fallen deshalb zu niedrig aus und sind entsprechend gekennzeichnet.

### Kennwerte lesen

- **Prozentuale Übereinstimmung** nie allein berichten.
- **Cohens κ** neben **Krippendorffs α** und **Gwets AC1**. Bei schiefer Verteilung kann κ trotz hoher Übereinstimmung niedrig sein (Kappa-Paradox); AC1 und PABAK helfen beim Deuten.
- Krippendorff (2018) nennt α ≥ 0,800 belastbar und α ≥ 0,667 vorläufig. Solche Schwellen sind Konventionen, keine Gesetze.
- **Mensch-Mensch-Baseline:** Wie gut stimmen die menschlichen Kodierenden untereinander überein? Ein LLM, das an die Baseline heranreicht, ist so gut wie eine weitere geschulte Person.
- **Alt-Test** (Calderon et al. 2025, ab 3 Kodierenden): prüft, ob das LLM eine menschliche Kodierperson ersetzen kann. ε nach Vergleichsgruppe wählen: Expert:innen 0,2, geschulte Kodierende 0,15, Crowd 0,1.
- **Je Code:** Precision, Recall und F1. Codes mit weniger als 20 Referenzfällen sind markiert; ihre Werte schwanken stark. Die Konfusionsmatrix lässt sich zeilenweise in Prozent anzeigen; auf der Diagonale steht dann der Recall je Referenz-Code.
- **κ und ρ je Code** (Tab Evaluation, Tabelle „Details: LLM vs. … (je Code)“ unten im Bericht; die Auswahl „Details … für“ links springt dorthin): κ für den Code als ja/nein und **Shaffers ρ** (Shaffer 2017; nachgebaut nach dem R-Paket rhoR): der Anteil simulierter Test-Sets gleicher Größe aus Grundgesamtheiten mit κ zwischen 0,40 und 0,65, die das beobachtete κ erreichen. ρ < 0,05 spricht dafür, dass κ über alle Einheiten über 0,65 liegt. Die Werte stimmen mit rhoR überein (geprüft gegen rhoR 1.3.1). Grenzen: Die Fehlerrate von 5 % gilt gemittelt über diesen Bereich, ρ ist kein Test gegen genau 0,65. Liegt das wahre κ knapp unter 0,65, sagt ρ deutlich öfter fälschlich „darüber“, und zwar umso öfter, je größer das Test-Set (in Simulationen rund 10–20 % bei 50 Einheiten, 30–50 % bei 500). ρ setzt unabhängige Einheiten voraus; mit Gruppen fällt es zu klein aus. Ab 10.000 verglichenen Einheiten gibt es kein ρ (wie in rhoR).
- **Methodenabsatz kopieren** (Deutsch oder Englisch) legt den Absatz für den Methodenteil in die Zwischenablage, mit Kennwerten, sobald eine aktuelle Auswertung berechnet ist.
- **Kalibrierung:** Taugt die Konfidenz dazu, unsichere Fälle zu finden? Je Signal (Self-Consistency, verbalisierte Konfidenz, im Panel der Anteil zustimmender Modelle):
  - **AUROC** trennt richtige von falschen Kodierungen (0,5 = gar nicht); **AURC** ist die mittlere Fehlerrate, wenn man die Einheiten nach absteigender Konfidenz annimmt.
  - **ECE** misst die Abweichung von Konfidenz und Trefferquote; **ECE rekalibriert** zeigt, wie gut sie nach einer isotonen Rekalibrierung wäre (fünffach kreuzvalidiert). Verbalisierte Konfidenz ist oft schlecht kalibriert, und nach ihr zu filtern verzerrt Schätzungen (Wang, Deng & Yang 2026): nie unkalibriert als Schwelle verwenden.
  - Die Evaluation zeigt dazu je Signal das **Reliability-Diagramm** (Trefferquote je Konfidenzklasse gegen die Diagonale) und die **Risk-Coverage-Kurve** (Fehlerrate der angenommenen Einheiten; gestrichelt die Fehlerrate ohne Auswahl). Liegt die Kurve links deutlich unter der Linie, taugt die Konfidenz als Filter.
  - **Übereinstimmung der Läufe:** Trefferquote je Stufe, etwa 3/3 neben 5/5. Der adaptive Abbruch gibt beiden Self-Consistency 1,0, sie sind aber nicht gleich sicher.
- **Intervalle:** 95-%-Bootstrap-Intervalle, mit Gruppen-Spalte gruppenweise gezogen (auch im isolierten Modus), haben Übereinstimmung, Cohens κ (auch gewichtet), Krippendorffs α, Gwets AC1, Scotts π, PABAK, Accuracy, Macro-F1, MCC, Top-k, die Werte auf Ebene der Code-Gruppen und die PPI-Prävalenz. Punktwerte ohne Intervall sind die Werte je Code, Micro-F1, Exact Match, Jaccard, Hamming-Loss, Abstain-Rate, Coverage, Kalibrierung und Stabilität.
- **Subgruppen** (Sprecher, Gruppe, Metadaten) zeigen, ob das LLM für manche Teile der Daten schlechter kodiert.
- **Messfehler nach Subgruppen** prüft das Programm immer, für jede Subgruppen-Variable: Hängt die Fehlerrate gegen die Hauptreferenz von der Variable ab (Wald-Test, cluster-robust, Holm-korrigiert)? Wenn ja, wären Analysen nach dieser Variable mit LLM-Codes verzerrt, auch bei hoher Gesamtgüte (Egami et al. 2023). Dann menschliche Labels einbeziehen (PPI, dsl). Ausprägungen mit weniger als 5 Einheiten entfallen; die Gruppe selbst entfällt, wenn nach ihr geclustert wird.

### Prävalenz: PPI und dsl

Wer Anteile von Codes berichten will, sollte nicht einfach die LLM-Labels auszählen, denn systematische Fehler verschieben die Anteile.
- **PPI++** (Angelopoulos, Duchi & Zrnic 2023) korrigiert die LLM-Anteile mit der zufälligen Review-Stichprobe. Das Ergebnis ist unverzerrt. Das Power-Tuning λ gewichtet die LLM-Labels so, dass das Intervall asymptotisch nie breiter ist als das der Stichprobe allein (λ = 0) oder das von PPI (λ = 1). Mit Gruppen-Spalte ist das Intervall cluster-robust über die Transkripte, weil Einheiten eines Transkripts zusammenhängen. Die Evaluation zeigt PPI automatisch, sobald blinde Reviews der Zufallsstichprobe vorliegen, als Tabelle und als Grafik (Punkt und 95-%-Intervall je Schätzer).
- **Alle Reviews gewichtet** (confidence-driven inference, Gligorić et al. 2025): PPI++ nutzt nur die Zufallsstichprobe, weil das Gate gezielt schwierige Fälle wählt. Die gewichtete Schätzung nutzt zusätzlich die blinden Reviews aus dem Gate und gewichtet jede mit ihrer Ziehwahrscheinlichkeit in der aktuellen Queue (Gate 1, Zufallsstichprobe n/N), dazu Power-Tuning wie bei PPI++. Sie ist dann unverzerrt und meist deutlich enger, weil die Reviews dort liegen, wo das LLM am häufigsten irrt. Bedingungen, die das Programm prüft: ein Zufallsanteil über 0 (sonst bliebe der Fehler außerhalb des Gates unkorrigiert) und eine **vollständig abgearbeitete Queue**; solange Einheiten der Queue ohne blinde Review sind, fehlt die Spalte, und die Evaluation sagt, wie viele noch offen sind. Es zählen nur Einheiten der aktuellen Queue: Wird sie neu gezogen (andere Schwelle, anderer Anteil oder Seed), gehören Reviews früherer Ziehungen nicht mehr dazu. Reviews außerhalb der Queue zählen ebenfalls nicht.
- Für beide Schätzer gilt: Power-Tuning erst ab 20 zufällig gezogenen, reviewten Einheiten, darunter λ = 1 (sonst wird das Intervall zu schmal). Cluster-robuste Intervalle nutzen das t-Quantil mit G − 1 Freiheitsgraden (G = Zahl der Gruppen). Die Intervalle beziehen sich auf die Prävalenz in der Grundgesamtheit, aus der die Einheiten stammen; für genau die kodierten Einheiten sind sie etwas zu breit.
- Für Regressionen enthält der Export nach R die Spalten für `dsl` (Egami et al. 2023). Das mitgelieferte R-Skript zeigt ein Beispiel.

### Begründungen

Begründungen des Modells sind nachträgliche Plausibilisierungen, keine Erklärung seiner Entscheidung (Turpin et al. 2023). Prüfbar sind die Signalwörter: Sie müssen wörtlich im Text stehen, sonst wird die Antwort als „nicht belegt“ markiert.

## Datenschutz

- Lokal ist der Default. Vor jedem Lauf mit einem externen Anbieter fragt das Programm nach Zustimmung und vermerkt sie im Protokoll. „Lokal“ gilt nur, wenn die Adresse dieser Rechner ist (localhost, 127.0.0.1); ein Ollama-Server im Netz zählt als extern, auch wenn er als lokal eingetragen ist. Geht ein API-Schlüssel unverschlüsselt (http) an einen anderen Rechner, warnen Modelle-Tab und Zustimmungsdialog.
- Vorher prüfen, ob die Daten pseudonymisiert sind und ob der Anbieter für sie zulässig ist.
- **Namen vor dem Senden ersetzen** (Tab Prompt & Einheiten → Namen ersetzen…): eine Liste Original → Ersatz, etwa „Lena“ → „Person 1“. „Sprecher übernehmen“ trägt die Werte der Sprecher-Spalte ein, außer Kürzeln bis zwei Zeichen wie „I“ oder „L“ (Rollen; ersetzt würde sonst jedes solche Wort im Text). „Aus Datei…“ liest eine Tabelle (erste Spalte Original, zweite Ersatz) oder die `pseudonyme.tsv` eines anderen Projekts. Die Spalte Treffer zeigt, wie oft ein Name in Text, Sprecher und Metadaten steht, in Klammern die Treffer in anderer Schreibweise („lena“, „LENA“), die nicht ersetzt werden; dafür eigene Einträge anlegen.
  - Ersetzt wird in Text, Sprecher und Metadaten, als ganzes Wort und mit Genitiv-s („Lenas“ → „Person 1s“); „Timo“ bleibt bei „Tim“ unberührt, Groß- und Kleinschreibung zählt. Unicode-Varianten desselben Namens (etwa aus PDF oder macOS) und Namen aus mehreren Wörtern mit geschütztem Leerzeichen oder Zeilenumbruch werden erkannt. Ein Ersatz darf selbst keinen Namen der Liste enthalten.
  - **Codebuch und IDs bleiben unverändert** und gehen so an das Modell: Namen in Ankerbeispielen, Einheiten- oder Gruppen-IDs vorher entfernen.
  - Die Vorschau zeigt den ersetzten Prompt. Daten, Ergebnisse und Exporte behalten die Originale; Signalwörter werden im Originaltext markiert, Begründungen und Signalwörter selbst nennen die Pseudonyme.
  - Die Liste liegt als `pseudonyme.tsv` im Projektordner, nicht in `project.json`, und kommt nur mit den Rohtexten ins Replikationspaket; ohne sie braucht der Rerun `--pseudonyme`. Frühere Fassungen bleiben in `pseudonyme_alt/`, damit sich auch ältere Pakete neu rechnen lassen. Verlangt ein Löschkonzept, die Zuordnung zu löschen, beide entfernen: Auswertung, Report und Exporte gehen weiter, nur ohne Beispiel einer Nutzer-Nachricht; neue Läufe und die Vorschau starten dann nicht mehr.
  - Eine Änderung der Liste startet beim nächsten Kodieren einen neuen Lauf und bricht einen Freeze; der Dialog warnt davor. Wird die Datei außerhalb des Programms geändert oder gelöscht, startet kein Lauf mehr, bis sie im Tab neu gespeichert ist; so gehen Namen nicht unbemerkt hinaus.
  - Eine Liste erkennt nur die Namen, die in ihr stehen; Spitznamen, Tippfehler und andere Schreibweisen gehören einzeln hinein.
- Das **Replikationspaket** enthält ohne den Schalter „Rohtexte einschließen“ nur IDs, Codes und Kennzahlen; Texte, Metadaten, Roh-Antworten, Begründungen, Signalwörter und Review-Notizen fehlen dann. Sprecher und Review-Personen stehen als Pseudonyme (S01, R01 …) im Paket, und Fehlermeldungen im Protokoll verlieren den zitierten Antworttext des Anbieters. Hashes einzelner Texte oder Prompts fehlen, weil sich kurze Texte wie „Ja.“ erraten ließen; die gemeinsame Prüfsumme aller Einheiten steht in `project.json`. Einheiten- und Gruppen-IDs bleiben unverändert, außer man setzt den Schalter „Einheiten- und Gruppen-IDs pseudonymisieren“: Dann stehen sie als E01, G01 … im Paket. Die Zuordnung steht nicht im Paket; `llmanno-rerun` rechnet sie aus den Originaldaten nach (gleiche Zeilen, gleiche Reihenfolge) und vergleicht wie gewohnt.

## Exporte

Report, Präregistrierung, GUIDE-LLM-Checkliste und die Beschreibung im Replikationspaket gibt es auf Deutsch und Englisch: **Sprache → Report, Präregistrierung, Replikationspaket**. Die Wahl gilt sofort und unabhängig von der Sprache der Oberfläche. Der Methoden-Report enthält den Methodenabsatz immer in beiden Sprachen, die gewählte zuerst; der Prompt im Anhang steht in der Sprache des Prompt-Templates. Hinweise der Evaluation erscheinen in der Sprache der Oberfläche.

| Export | Wo | Inhalt |
|---|---|---|
| Ergebnisse (XLSX) | Export | eine Zeile je Einheit samt Split (dev/test) und Review-Status (blind, assistiert, offen), alle Einzelläufe, Codes, Reviews, Hashes |
| REFI-QDA (.qdpx) | Export | für MAXQDA, ATLAS.ti, QualCoder, NVivo (siehe unten) |
| Codesystem (.qdc) | Export | nur die Codes |
| Label Studio (.json und .xml) | Export | je Einheit eine Aufgabe mit Zieltext und Kontext, auf Wunsch mit LLM-Vorschlägen (finale Codes, Signalwörter als Spans); die .xml unter Settings → Labeling Interface einfügen, die .json importieren. Vorschläge lenken das Urteil wie eine assistierte Review. Gold steht nicht darin; vor dem Freeze mit „nur Dev“ ohne Vorschläge für Test-Einheiten. INCEpTION ist nicht eingebaut: Es erwartet eigene Schichten und Typsysteme; für QDA-Programme gibt es REFI-QDA. |
| Replikationspaket (.zip) | Export | alles für Nachvollzug und Rerun |
| Kennwerte (XLSX) | Evaluation | alle Kennwerte mit Intervallen, je Code, Konfusion, Kalibrierung, Alt-Test, Subgruppen, Messfehler, PPI |
| Methoden-Report (Word oder HTML) | Evaluation oder Export | Bericht samt Methodenabsatz auf Deutsch und Englisch und vorausgefüllter GUIDE-LLM-Checkliste; das HTML zeigt Reliability-Diagramm, Risk-Coverage-Kurve und Prävalenz als Grafik, Word die Zahlen als Tabelle. Geht auch ohne Auswertung (Pilotphase), dann ohne Kennwerte. |
| SPSS, Stata, R | Evaluation | eine Zeile je Einheit, Codes mit Wertelabels, Split und Review-Status (zum Filtern etwa auf das Test-Set), mit Review-Queue auch die `dsl`-Spalten |
| Präregistrierung (Markdown oder Word) | Splits & Freeze | eingefrorenes Instrument und Analyseplan; Word für OSF und Ethikanträge |

### REFI-QDA in QDA-Programmen

Das Projekt importieren Programme, die den REFI-QDA-Standard unterstützen, über ihren Import für REFI-QDA-Projekte. Darin:

- **Texte:** eine Textquelle je Gruppe, jede Zeile als „[ID] Sprecher: Text“.
- **Personen:** „LLM-Panel“, jede Gold-Spalte und jede Review-Person stehen als eigene Personen im Projekt. So rechnet das QDA-Programm die Intercoder-Übereinstimmung LLM gegen Mensch mit seiner eigenen Funktion nach.
- **Codes:** die Codes des Codebuchs. Die Signalwörter stehen im eigenen Zweig „LLM-Evidenz“, damit sie den Vergleich der Hauptcodes nicht verfälschen.
- **Notizen:** je Einheit eine Notiz mit Top-k, beiden Konfidenzen, Begründung und Flags; dazu das Codebuch wörtlich.

Der Import in QualCoder ist geprüft; MAXQDA, ATLAS.ti und NVivo sind noch nicht abgenommen, Rückmeldungen helfen.

**Gold aus dem QDA-Programm zurückholen:** Wer lieber im QDA-Programm kodiert, exportiert das Projekt als REFI-QDA, kodiert dort die Texte (jede Person mit eigenem Benutzer), exportiert wieder als `.qdpx` und wählt im Tab Daten **Gold aus QDA-Projekt…**. Vorausgewählt sind neue menschliche Kodierende, nicht das LLM und keine schon vorhandene Gold-Spalte. Jede gewählte Person wird eine Gold-Spalte „<Name> (QDA)“. Eine Kodierung zählt für jede Einheit, deren Zeile sie überlappt; bei Single-Label gilt der Code mit dem größten Anteil. Codes ordnet das Programm über den Namen zu („K1“ oder „K1 …“); Codes ohne Gegenstück im Codebuch nennt der Prüfbericht. Die Einheiten findet es über die Marker „[ID]“ im Text; die Texte im QDA-Programm also nicht umschreiben. Übernommen wird wie beim Gold-Nachtrag: Lauf-Identitäten und Freeze bleiben, die bisherige Datenkopie bleibt als Sicherung. Ein zweiter Import derselben Person ersetzt ihre Spalte. Die Spalten heißen wie die Benutzer im QDA-Programm und stehen so auch im Replikationspaket ohne Rohtexte; dort also Kürzel statt Klarnamen verwenden. CSV-Projekte bekommen die Daten mit den neuen Spalten als TSV zurück.

### GUIDE-LLM-Checkliste

Report und Replikationspaket (`guide_llm.md`) enthalten die Berichtscheckliste GUIDE-LLM (Feuerriegel et al. 2026, *Nature Human Behaviour*, [llm-checklist.com](https://llm-checklist.com)): 14 Kernpunkte von Zweck über Modelle, Parameter, Prompts und Datenschutz bis Validierung, dazu empfohlene Zusatzangaben. Das Programm füllt aus, was es aus Projekt, Audit-Log und Auswertung weiß, auch die Zahl der Lauf-Identitäten (wie oft Prompt, Codebuch oder Parameter geändert und erneut kodiert wurden). Zeilen mit „→ ergänzen“ selbst ausfüllen, etwa Interessenkonflikte, Ethik und die Begründung der Modellwahl.

## Replikation

```bash
uv run llmanno-rerun replikation.zip --out rerun
```

Der Rerun legt ein neues Projekt an, kodiert mit denselben Einstellungen und Seeds und vergleicht jede Entscheidung mit dem Original. Hat sich das Modell-Build geändert (anderer Digest), wird das gemeldet. Ohne Rohtexte im Paket die Originaldaten mit `--data` angeben; das Programm prüft sie gegen den Einheiten-Hash. Hat das Original Namen ersetzt, gehört die Namensliste aus dem Projektordner dazu (`--pseudonyme pseudonyme.tsv`); sie muss dieselbe sein. Externe Anbieter brauchen `--consent`.

## Fehlerbehebung

| Meldung | Ursache und Abhilfe |
|---|---|
| „Ollama ist nicht erreichbar“ | `ollama serve` starten |
| „Modell … ist in Ollama nicht installiert“ | Modell mit `ollama pull …` laden oder den Namen prüfen |
| „Prompt vermutlich gekürzt“ | Der Lauf bricht ab, weil der Prompt nicht ins Kontextfenster passt und Ollama ihn sonst still kürzen würde. Kürzeres Codebuch, weniger Kontextzeilen oder ein Modell mit größerem Fenster |
| Sehr langsamer Lauf | Modell passt nicht in den Grafikspeicher. Kleineres Modell wählen oder eine API nutzen. Bei kleinen Modellen (unter 10 Mrd. Parameter) ist die Nachfrage (Repair) ohnehin aus, sobald sie ins Panel kommen |
| „Freeze gebrochen“ | Nach dem Freeze wurde etwas geändert. Änderung zurücknehmen oder den Freeze mit Begründung aufheben |
| „Das Test-Set ist bis zum Freeze gesperrt“ | Vorher nur Dev auswerten, dann einfrieren |
| „Das Modell erlaubt nur seine eigene Temperatur“ | Betrifft etwa GPT-5 und OpenAIs o-Modelle. Im Tab Modelle in der Spalte Temperatur „nicht senden“ wählen (kleinster Wert). Dass solche Modelle `max_completion_tokens` statt `max_tokens` verlangen, stellt das Programm selbst um. |
| „… verlangt N min Pause (Retry-After), vermutlich ist das Kontingent erschöpft“ | Das Tages- oder Stundenkontingent beim Anbieter ist aufgebraucht. Später fortsetzen; im Tab Modelle die Anfragen pro Minute begrenzen |
| HTTP 401 oder 403 | API-Key fehlt, ist abgelaufen oder gilt nicht für dieses Modell. Im Tab Modelle **Key entfernen** und neu speichern |
| „Das Projekt stammt aus einer neueren llmanno-Version“ oder „… Einstellungen, die diese llmanno-Version nicht kennt“ | Das Projekt wurde mit einer neueren Version gespeichert. llmanno aktualisieren |
| Store-Migration | Öffnet eine neuere llmanno-Version ein älteres Projekt, sichert sie die Datenbank vorher als `store.vor-migration-<n>.sqlite` im Projektordner |

## Glossar

Die Oberfläche nennt zuerst den Klartext und in Klammern das Fachwort, das auch im Methodenteil und in der Literatur steht.

- **Analyseeinheit:** was das Modell auf einmal kodiert: eine Zeile, eine Zeile mit Kontext oder eine ganze Gruppe (etwa ein Transkript).
- **Kontext (Kontextfenster):** Nachbarzeilen derselben Gruppe, die das Modell mitliest, aber nicht kodiert.
- **Gold (Referenzkodierung):** die Kodierungen von Menschen, an denen die Qualität des LLM gemessen wird, eine Spalte je Person, optional ein Konsens.
- **Codebuch und Code-Liste:** Das Codebuch ist der Text mit Definitionen, Regeln und Ankerbeispielen; er geht unverändert an das Modell. Die Code-Liste legt fest, welche Kürzel das Modell vergeben darf und welche ausgewertet werden.
- **Restkategorie (KEIN_CODE):** erlaubt dem Modell zu sagen, dass kein Code passt, statt einen zu erzwingen.
- **Kodiermodus (Single-/Multi-Label):** ein Code je Einheit (weitere nur als Alternativen) oder mehrere Codes je Einheit.
- **Anweisungen (Prompt):** was das Modell bekommt: die System-Nachricht mit Aufgabe und Codebuch (für alle Einheiten gleich) und die User-Nachricht mit dem Text der Einheit.
- **Anbieter (Provider) und Modell:** wer das Modell betreibt (Ollama auf dem eigenen Rechner, GWDG/KISSKI, KI:connect.NRW …) und welches Modell kodiert.
- **Panel:** mehrere Modelle, die dieselben Einheiten kodieren; entschieden wird gemeinsam, jedes Modell zählt gleich.
- **Durchläufe (Läufe, N):** wie oft jedes Modell dieselbe Einheit kodiert. Ein **Lauf** im Tab Lauf (Pilot, Vollauf) kodiert alle gewählten Einheiten mit allen Durchläufen.
- **Konsistenz (Self-Consistency, p_sc):** Anteil der Durchläufe aller Modelle, die denselben Code liefern. Das wichtigste Maß für die Unsicherheit einer Einheit.
- **Selbsteinschätzung (p_verbal):** die Konfidenz, die das Modell selbst nennt; meist zu optimistisch.
- **Adaptiver Stopp:** beendet die Durchläufe einer Einheit, sobald genug von ihnen denselben Code liefern.
- **Startwert (Seed):** macht Zufall wiederholbar, etwa die Reihenfolge der Codes oder die Ziehung einer Stichprobe.
- **Temperatur:** wie stark die Antworten eines Modells streuen; 0 heißt möglichst gleich.
- **Reasoning-Aufwand:** wie lange ein denkendes Modell (gpt-oss, GPT-5, Qwen3) vor der Antwort nachdenkt.
- **Token:** Textbausteine, meist Wortteile, nach denen Anbieter abrechnen und Längen begrenzen.
- **Kontingent:** wie viele Anfragen oder Token ein Anbieter pro Minute, Tag oder Monat erlaubt.
- **Signalwörter (Belege):** Wörter aus dem Text, mit denen das Modell seinen Code belegt; das Programm prüft, ob sie wörtlich vorkommen.
- **Nachfrage (Repair):** Ist eine Antwort unbrauchbar oder fehlen die Belege im Text, fragt das Programm einmal nach.
- **Übungsteil (Dev) und Prüfteil (Test), Aufteilung (Split):** Am Übungsteil werden Codebuch und Prompt verbessert, so oft man will. Der Prüfteil bleibt bis zum Einfrieren gesperrt und misst am Ende unabhängig, wie gut das Instrument ist.
- **Instrument:** Codebuch, Code-Liste, Prompt, Modelle und ihre Einstellungen zusammen.
- **Einfrieren (Freeze):** schreibt das Instrument fest, bevor der Prüfteil ausgewertet wird. Wer danach etwas ändert, bricht den Freeze; das steht im Report.
- **Präregistrierung:** der öffentlich hinterlegte Analyseplan (etwa bei OSF), bevor man das Ergebnis kennt.
- **Pilot:** ein kurzer Lauf mit 20 Einheiten, um Modell und Prompt zu testen und die Dauer hochzurechnen.
- **Prompt-Sensitivität:** prüft, ob die Codes vom Wortlaut der Anweisungen abhängen.
- **Prüfliste (Review-Queue):** Einheiten, die Menschen prüfen: unsichere Fälle und eine Zufallsstichprobe.
- **Auswahl unsicherer Fälle (Gate), Schwelle τ:** schlägt Einheiten zur Prüfung vor, deren Konsistenz unter τ liegt oder die andere Warnzeichen tragen.
- **Blind oder assistiert:** blind ohne LLM-Vorschlag (zählt als Gold), assistiert mit sichtbarem Vorschlag (zählt nicht als Gold).
- **Kennwerte der Übereinstimmung:** Prozent Übereinstimmung; **Cohens κ** und **Krippendorffs α** bereinigen um Zufall (α ab 0,80 belastbar, ab 0,667 vorläufig); **Gwets AC1** ist robust, wenn Codes sehr ungleich häufig sind; **F1** misst je Code, wie gut Treffer und Auslassungen ausbalanciert sind.
- **Konfidenzintervall:** die Werte in eckigen Klammern; der Bereich, in dem der wahre Wert mit 95 % Sicherheit liegt.
- **Prävalenz und PPI++:** wie häufig ein Code vorkommt, korrigiert mit den blinden Reviews der Zufallsstichprobe.
- **Codesystem:** ein weiteres Codebuch auf denselben Daten, mit eigener Auswertung.
- **REFI-QDA:** Austauschformat für QDA-Programme (MAXQDA, ATLAS.ti, NVivo, QualCoder).
- **Replikationspaket:** ZIP mit allem, was andere zum Nachprüfen und Neurechnen brauchen.
- **Protokoll (Audit-Log):** unveränderliche Liste aller Schritte im Projekt; Grundlage von Report und Replikationspaket.
