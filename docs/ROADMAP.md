# Roadmap llmanno: was noch ansteht

Stand 2026-10-06. Ergebnis einer Bestandsaufnahme mit sechs Blickwinkeln (PLAN gegen Code, Oberfläche, Code-Schulden und Tests, Publikationsreife, Anwendungsfälle, Skalierung) und einem Kritiker; jeder Befund wurde gegen den Code gegengeprüft. 98 Befunde, zusammengefasst zu 45 Punkten, keiner gestrichen. Pfade sind relativ zu `src/llmanno/`, außer `docs/`, `packaging/`, `pyproject.toml` und `README.md`. Aufwand: S = klein, M = mittel, L = groß.

**Vorschlag für die Reihenfolge:** Zuerst die kleinen Fixes aus Teil 1 (2, 3, 4, 8, 9). Dann Punkt 7 als Vorarbeit, danach 5, 6, 1 und 10.

---

## 1. Vor der ersten Veröffentlichung nötig

### Ergebnisse und Report zeigen, was wirklich gelaufen ist
1. **Report und GUIDE-LLM aus dem Lauf statt aus der aktuellen Konfiguration** · M · ✓ erledigt (`86c657c`) · `core/export/report.py:145-160, 486-497`, `core/export/guide.py:65-87`
   Ändert man nach einem Lauf das Panel, nennt der Methodenabsatz ein Modell, das nie gelaufen ist, und falsche Werte für Temperatur, max_tokens und N. Dafür muss `run_started` alle Modellparameter protokollieren (`core/engine.py:381-391`).
2. **Alte Evaluation bleibt nach Projektwechsel und neuen Läufen stehen** · S · ✓ erledigt (`e0f5164`) · `gui/evaluation_page.py:77, 176-205, 293, 860`, `gui/export_page.py:128`
   Report, Kennwerte-XLSX und `metrics.json` von Projekt B enthalten dann die Kennwerte von Projekt A. Eine noch laufende Berechnung landet ebenfalls im neuen Projekt.
3. **Sensitivitätsläufe ohne Hauptlauf erscheinen als Hauptergebnis** · S · ✓ erledigt (`8848eeb`) · `core/store.py:356-361`, `core/engine.py:392-401`
   Die Anleitung empfiehlt, die Sensitivität vor dem Freeze zu prüfen. Dann zeigen Ergebnisse, Review und Exporte die Ein-Lauf-Antworten einer Prompt-Variante als Kodierung (nachgestellt). Ein Regressionstest fehlt.
4. **Unvollständiges Panel gilt still als vollständig** · S · ✓ erledigt (`d6b3a69`) · `core/aggregate.py:76-77, 114-137`, `core/evaluation.py:803-807`
   Bricht ein Modell ab, stehen Einheiten mit 1 von 3 Stimmen als „ok“ da. Es gibt kein Flag und keine Warnung in Evaluation oder Report.

### Gold und Referenz
5. **Gold-Zuordnung und Trennzeichen in den Daten-Tab; Split schließt nicht zugeordnete Gold-Werte still aus** · M · ✓ erledigt (`b235b3e`) · `core/validation.py:58-59`, `gui/evaluation_page.py:92-118, 307-341`, `gui/codebook_page.py:259`
   Gold-Werte wie „1“ oder „2“ fallen ohne Hinweis aus Dev und Test. Zuordnen lassen sie sich erst nach einer Evaluation, oft also nach dem Freeze. „Codes aus Gold“ arbeitet mit fest eingebautem „;“ statt mit `gold.separator` und schlägt deshalb „K1_K2“ vor. Umgesetzt im Tab Codebuch statt im Daten-Tab, weil die Zuordnung die Code-Liste braucht.
6. **Gold lässt sich nach dem ersten Aufruf nicht mehr ändern, weil es im Instrument-Hash steckt** · M · ✓ erledigt (`61a0f4b`) · `gui/data_page.py:110-118`, `core/data_import.py:173-175`, `core/engine.py:155, 328`
   Kommt Coder B nach dem Pilot dazu oder muss Gold korrigiert werden, bleibt nur ein neues Projekt mit komplettem Neulauf.

### Vorarbeit für die nächsten Schema-Änderungen
7. **Hash-Stabilität absichern: Golden-Tests für Fingerabdruck, config_hash und gerenderten Prompt** · M · ✓ erledigt (`ad4b492`) · `core/engine.py:104-119, 144-162, 318-347`, `core/prompt.py:18`
   Jedes neue Feld mit Default, etwa `Code.parent` für Punkt 10, bricht alle Freezes und erzwingt eine Neukodierung. Eine Prompt-Änderung ohne Sprung der `TEMPLATE_VERSION` bleibt unbemerkt. Ich habe den Punkt von „sollte“ hochgestuft, weil er Voraussetzung für Punkt 6 und 10 ist.

### Einfrieren und Hänger
8. **XLSX-Export wird quadratisch langsamer** · S · ✓ erledigt (`19703fd`) · `core/export/xlsx.py:224-229`
   Gemessen: 85 s bei 3.000 Einheiten, 276 s bei rund 24.000 Aufrufen. Der Export läuft im GUI-Thread, und der Fix ist eine Zeile.
9. **Retry-After ohne Obergrenze; Anhalten und Schließen hängen** · S · ✓ erledigt (`195d518`) · `core/providers/base.py:72-82`, `core/engine.py:415, 614-623`, `gui/run_page.py:308-311`
   Bei Retry-After 3600 dauert es 20 h bis zum Abbruch, und „Anhalten“ friert das Fenster ein. Abhilfe: Wartezeit deckeln und unterbrechbar warten (Event.wait).

### Von dir angefragt
10. **Code-Gruppen und Auswertung auf Ebene der Oberkategorien** · M · ✓ erledigt (`bf020bb`) · `core/config.py:45`, `gui/codebook_page.py:297-308`, `core/evaluation.py:203ff`
    Für Felton & Kuhn (8 Codes auf 4 Diskursmodi) gibt es in der App keinen Weg. `parent` geht beim Speichern der Tabelle sogar verloren.

---

## 2. Sollte bald kommen

### Messung transparent machen
11. **Ausgeschlossene Modelle und veraltete Lauf-Grundlage nicht sichtbar; Grundlage an mehreren Stellen verschieden bestimmt** · S · ✓ erledigt (`83fc1ab`) · `core/engine.py:708-739`, `core/evaluation.py:743`, `gui/results_page.py:124-129`
    `excluded` und `current` liest niemand. Stabilität und Detailansicht nehmen ausgeschlossene Modelle trotzdem mit. Alle Aufrufer sollten über `result_basis()` laufen und bei Abweichung warnen.
12. **Messbedingungen protokollieren: Ausgabemodus/Fallback, Ollama-Version, Quantisierung, Parametergröße** · S · ✓ erledigt (`f1e0694`) · `core/engine.py:381-391, 698`, `core/providers/ollama.py:95`, `core/export/replication.py:49-65`
    PLAN 3.4 und 3.13 verlangen diese Angaben in Log, Report und `environment.txt`, und GUIDE-LLM fragt danach. `version()` wird nirgends aufgerufen.
13. **Kennwerte ohne Konfidenzintervall oder gar nicht angezeigt** · M · ✓ erledigt (`a18fa55`) · `core/evaluation.py:69-71, 108, 569`, `core/metrics/agreement.py:235`, `docs/ANLEITUNG.md:103`
    Scott π, PABAK, gewichtetes κ, MCC und Top-k haben kein KI. Abstain-Rate, Coverage, Hamming und Jaccard stehen nur im XLSX. Die Anleitung behauptet trotzdem KIs für alle Werte.
14. **Codebuch-Versionierung mit Historie und Diff** · M · ✓ erledigt (`baea83c`) · `core/project.py:129-146`, `core/export/report.py:458-467`
    `codebook.txt` wird überschrieben, deshalb ist die Dev-Schleife im Report nicht nachvollziehbar. PLAN 3.3 sagt das zu, und es schützt gegen den Vorwurf des LLM-Hackings.
15. **Cluster-Bootstrap und gruppierter Split auch im isolierten Modus** · S · ✓ erledigt (`bf1af12`) · `core/evaluation.py:246`, `core/validation.py:66`
    Isolierte Moves aus wenigen Transkripten gelten als unabhängig. Die KIs werden zu schmal, Wald-Test und PPI zu liberal.

### Daten, Codebuch, Einheiten
16. **Stille Zeichen- und Importfehler (NFC, Reihenfolge, Codebuch-Kodierung)** · S · ✓ erledigt (`5032f09`) · `core/evidence.py:103-125`, `core/data_import.py:127-139`, `core/codebook.py:45-57`
    Zerlegte Umlaute (NFD) gelten als „nicht belegt“. Die Reihenfolge 1.1/1.10 vertauscht still den Kontext. Ein UTF-16-Codebuch wird ohne Meldung zu NUL-Zeichensalat im Prompt, und eine .docx-Datei gibt einen kryptischen charmap-Fehler.
17. **Code-Liste gegen das Codebuch prüfen und dessen Restkategorie erkennen** · S · ✓ erledigt (`6c7fab6`) · `core/codebook.py:90-100`, `core/prompt.py:400`, `gui/codebook_page.py:311-343`
    Codes ohne Label und ohne Definition gehen als bloßes „- 3“ in den Prompt. Eine eigene Restkategorie im Codebuch („Sonstiges“) konkurriert mit KEIN_CODE.
18. **Neue Spalten-Rollen: Analyse-Variable (nicht im Prompt) und „kodieren ja/nein“** · M · ✓ erledigt (`7744665`) · `core/config.py:17-29`, `core/evaluation.py:172-183`, `core/prompt.py:461-463`, `core/units.py:52-64`
    Subgruppen für den Messfehler-Test müssen heute in den Prompt. Das kann genau den geprüften Bias erzeugen und schickt Personenmerkmale an die API. Lehrkraft- oder Interviewer-Züge lassen sich nicht als reiner Kontext führen. Optional käme eine Zug-Spalte für das Kontextfenster dazu.
19. **Validiertes Instrument auf neue Daten übertragen** · M · ✓ erledigt (`d149bf5`) · `core/store.py:239-244`, `rerun.py:111-141`
    Eine neue Erhebungswelle braucht heute ein neues Projekt, in das alles von Hand übernommen wird. Gebraucht wird „Neues Projekt aus Instrument“, wie im Rerun, aber ohne Prüfung auf denselben Einheiten-Hash.
20. **Mehrere Codesysteme je Projekt (Reference Move als eigene Dimension)** · L · ✓ erledigt (Version 2; je Codesystem eigener Aufruf und eigenes Review, Entscheidung des Users 2026-10-07) · `core/codesystems.py`, `gui/main_window.py`, `gui/codesystem_dialog.py`
    Von dir angefragt. Der Umweg über ein zweites Projekt kostet einen doppelten Lauf sowie getrennte Reviews und Exporte.

### Dev-Schleife, Review, Freeze
21. **Abweichungsansicht LLM gegen Referenz im Ergebnis-Tab** · M · ✓ erledigt (`ec6f085`) · `gui/results_page.py:35-44, 57-73`, `gui/evaluation_page.py:910`
    Zum Schärfen des Codebuchs fehlen eine Referenzspalte und Filter für „weicht ab“, „nur Dev“ und Konfidenz. Der Umweg über XLSX wird als Test-Einsicht protokolliert. Test-Gold muss vor dem Freeze verborgen bleiben.
22. **Review absichern: Rückfrage beim Neuziehen, feste Personen-Identität, eine Schicht je Person** · M · ✓ erledigt (`83df22c`) · `gui/review_page.py:186-188, 369-378`, `core/review.py:129-141, 164-167`
    Ein Klick zieht mitten im Review eine neue Stichprobe. Wer als „Anna“ assistiert reviewt hat, kann als „anna“ blind reviewen, und das zählt als Gold. Bei zwei blinden Reviewern gibt es kein α, die zweite Review überschreibt die erste. Tastenkürzel reichen nur für 9 Codes.
23. **Exporte vor dem Freeze zählen ohne Warnung als Test-Einsicht** · S · ✓ erledigt (`a307efc`) · `core/validation.py:213-225`, `gui/export_page.py:103-110`
    Ein XLSX-Export erzeugt still einen Vermerk im Methoden-Report. Nötig sind eine Rückfrage wie bei der Test-Auswertung und eine Option „nur Dev“.

### Lauf
24. **Fehlerpolitik im Lauf: warten, abbrechen, anzeigen** · M · ✓ erledigt (`fcbb554`) · `core/engine.py:41-43, 412-431, 497, 568-578`, `gui/run_page.py:339-352`
    Ein Netzausfall von 5 Minuten beendet einen Nachtlauf. Systematisch ungültige Antworten laufen dagegen ohne Abbruch weiter, mit n_runs × 2 Aufrufen je Einheit. Fehlergründe zeigt der Lauf-Tab nicht, und nirgends steht, dass Fortsetzen die Fehler wiederholt.
25. **Trockenlauf vor dem Start: Token, Dauer, Kontextbudget** · M · ✓ erledigt (`ede15b3`) · `gui/prompt_page.py:218-242`, `gui/run_page.py:287-301`, `core/engine.py:290-304`
    PLAN 3.4 und 3.5 versprechen geschätzte Token im Zustimmungsdialog. Ein zu langer Prompt fällt bei API-Modellen erst durch HTTP-Fehler auf, und ein Ausreißer treibt `num_ctx` für alle Aufrufe hoch. Der Dialog zeigt die Datenschutz-Stufe roh („academic“). Ein Preisfeld wäre optional.
26. **Fortschritt und Restzeit falsch nach Fortsetzen und bei Sensitivität** · S · ✓ erledigt (`89ae8a8`) · `core/engine.py:380`, `core/sensitivity.py:72-84`, `gui/run_page.py:315-338`
    Nach dem Fortsetzen bleibt der Balken bei 26 % stehen, bei der Sensitivität springt er je Variante auf 0. Bei langen Läufen ist Fortsetzen der Normalfall.

### Stabilität und Tempo der Oberfläche
27. **Modelle-Tab: laufende Probe ungeschützt bei Schließen und Projektwechsel** · S · ✓ erledigt (`30471ea`) · `gui/main_window.py:162`, `gui/models_page.py:451-463, 492-506, 526-528`
    Schließen zerstört einen laufenden QThread, Qt bricht ab. Nach einem Projektwechsel überschreibt die Probe den gleichnamigen Anbieter im neuen Projekt.
28. **Große Projekte blockieren den GUI-Thread (Öffnen, Exporte, Queue, Review)** · M · ✓ erledigt (`cf0816e`) · `gui/main_window.py:150-156`, `gui/export_page.py:103-106`, `gui/evaluation_page.py:833-884`, `gui/review_page.py:317`
    Gemessen bei 20.000 Einheiten: Öffnen 17,5 s, DOCX 27 s, Replikationspaket 38 s, assistierter Review 6,9 s je Einheit. Exporte und Queue-Bau sollten über Task laufen, der Review nur die Aufrufe der Einheit laden.
29. **Projektsperre fehlt; Risiko auf Netzlaufwerk und in Cloud-Ordnern** · S · ✓ erledigt (`11ff19c`) · `core/project.py:46-57, 148-152`, `core/store.py:184`
    Zwei Instanzen überschreiben sich gegenseitig die `project.json`. SQLite-WAL ist auf Netzlaufwerken unzuverlässig, und OneDrive oder Sciebo synchronisieren `-wal` getrennt.
30. **Fehlerprotokoll in der Exe ohne Konsole** · S · ✓ erledigt (`e513313`) · `packaging/llmanno.spec:22`
    Ausnahmen in Slots verschwinden spurlos. Ein excepthook mit Dialog und Logdatei macht Fehlerberichte möglich.
31. **Dunkelmodus: helle Hintergründe mit heller Schrift** · S · ✓ erledigt (`c57a268`) · `gui/evaluation_page.py:69`, `gui/results_page.py:28, 251`
    Im dunklen Modus von Windows 11 sind Tabellenköpfe und markierte Signalwörter unlesbar.

### Datenschutz
32. **Datenschutz-Stufe wird nicht gegen die Adresse geprüft** · S · ✓ erledigt (`a157fec`) · `gui/models_page.py:402-423`, `core/engine.py:228-232`, `core/providers/openai_compat.py:33-36`
    Ein Ollama-Preset mit entfernter URL bleibt „lokal“. Dann erscheint kein Zustimmungsdialog, und der Report meldet „lokal“. Ein Bearer-Key geht ohne Warnung über http.
33. **Replikationspaket „ohne Rohtexte“ hält die Zusage nicht** · M · ✓ erledigt (`3b70fc0`) · `core/export/replication.py:169-171, 187, 191, 223`, `core/providers/base.py:79-96`
    Das Paket enthält ungesalzene Sprecher-Hashes, die sich per Wörterbuch umkehren lassen. Im `audit.jsonl` stehen außerdem Reviewer-Namen und Antwortausschnitte aus Fehlermeldungen.

### Englisch
34. **tr() in f-Strings: 21 Überschriften bleiben in der englischen Oberfläche deutsch** · S · ✓ erledigt (`9e89664`) · `gui/evaluation_page.py:351, 385, 429, 728 u. a.`, `gui/results_page.py:224-291`, `gui/review_page.py:429`
    lupdate erfasst tr() in f-Strings nicht. Dazu kommen „% Übereinst.“ ohne tr() und rohe Status-Schlüssel. Ein Test gegen die .ts-Datei verhindert, dass das wieder passiert.
35. **Report, Präregistrierung, GUIDE-LLM und Kernmeldungen nur deutsch** · L · vor der ersten Veröffentlichung (User, 2026-10-07) · ✓ erledigt (`20d5a50`) · `core/export/report.py:414, 824`, `core/export/prereg.py:226`, `core/export/guide.py:241`, `core/evaluation.py:767-836`
    Für OSF und internationale Journals nötig. Zweisprachig ist bisher nur der Methodenabsatz.

### Release-Hygiene (kein App-Code, vor dem ersten Push)
36. **environment.txt im Exe-Paket lückenhaft, Version nie erhöht** · S · ✓ erledigt (`4a9b401`) · `packaging/llmanno.spec:8`, `core/export/replication.py:49-52`, `pyproject.toml:3`
    In einem Paket aus der Exe fehlen scipy, pandas, rapidfuzz und httpx. Alle Stände tragen die Version 0.0.1.
37. **Lizenzlage und interne Notizen** · S · ✓ erledigt: Lizenz AGPL, interne Notizen vor der Veröffentlichung entfernt · `pyproject.toml:15`, `packaging/llmanno.spec:7-8`
    Ohne LICENSE ist der Code rechtlich nicht nutzbar. Die Exe bündelt Qt (LGPL) ohne Lizenztext und pyreadr (AGPL). Interne Notizen in Handoff und PLAN gehören nicht in ein öffentliches Repo.

---

## 3. Kann warten / Version 2
38. **Report im Export-Tab und ohne Auswertung; Grafiken und Vorschau in der Evaluation** · M · ✓ erledigt (`423384f`) · `gui/evaluation_page.py:204`, `core/export/report.py:831, 934`, `gui/export_page.py:22-40`
    In der Pilotphase lässt sich kein Report aus der Oberfläche erzeugen. In der Oberfläche fehlen Reliability-, Risk-Coverage- und Prävalenzplot, die zeilennormierte Konfusionsmatrix und der Methodenabsatz zum Kopieren.
39. **Export-Komfort: Split und Review-Status in XLSX und Statistik, Präregistrierung als DOCX** · S · ✓ erledigt (`3f50acf`) · `core/export/xlsx.py:62-69`, `core/export/stats.py:92-130`, `gui/split_page.py:402-407`
    Wer nur das Test-Set in R oder SPSS auswerten will, muss heute von Hand zusammenführen. Für OSF und Ethikanträge ist PDF oder DOCX üblich.
40. **Saubere Fehlermeldungen und Versionsschutz** · S · ✓ erledigt (`8e8323d`) · `core/project.py:51-56`, `gui/main_window.py:191`, `rerun.py:248`, `core/engine.py:593-606`, `core/store.py:207-209`
    Eine neuere project.json ergibt rohen Pydantic-Text, llmanno-rerun gibt bei kaputtem ZIP Tracebacks aus. Ein gescheiterter Repair zählt als „repariert“, und die Store-Migration legt kein Backup an.
41. **Grundausstattung der Oberfläche** · M · ✓ erledigt (`4beeb18`) · `gui/main_window.py:82-105`, `core/providers/__init__.py:20-25`, `gui/models_page.py:392-400`
    Es fehlen ein Menü Hilfe/Über (mit Version zum Zitieren), eine Protokoll-Ansicht und eine Liste zuletzt geöffneter Projekte. Ein API-Key lässt sich in der App nicht entfernen.
42. **Import erweitern: CSV, mehrere Dateien oder Blätter, später QDA-Import** · M (QDA: L) · ✓ erledigt: CSV und Zusammenführen (`81030cb`), Gold aus QDA-Projekten (`4fd853e`) · `gui/data_page.py:158-159`, `core/project.py:87-95`, `core/export/refi.py`
    Transkripte liegen oft je Stunde getrennt vor. Gold aus MAXQDA oder QualCoder muss heute von Hand übertragen werden.
43. **Leistung für sehr große Projekte** · L · ✓ erledigt (`13d258d`, `a601364`) · `core/engine.py:381-438`, `core/store.py:311-334`, `gui/results_page.py:56, 168-193`, `core/metrics/bootstrap.py:46`
    Die Aufrufe laufen strikt nacheinander, ein Dreier-Panel mit 20.000 Einheiten bräuchte etwa 4,5 Tage. Der Store lädt immer volle Zeilen, die Tabelle baut sich bei jedem Tastendruck neu auf, und der Bootstrap lässt sich nicht abbrechen. Bei 2.000 Einheiten ist das noch tragbar.
44. **Doku und Publikationsvorbereitung** · M · ✓ erledigt (`286ead5`); ORCID, Repository-URL und Zenodo-DOI in CITATION.cff erst bei der Veröffentlichung · `docs/PLAN.md:45, 535, 547, 561`, `README.md:13`, `docs/ANLEITUNG.md:15-25, 159-170`, `packaging/llmanno.spec:7`
    Der PLAN nennt ungenutzte Bibliotheken und einen falschen Rerun-Aufruf. Der Anleitung fehlen die Installation der Exe, KI:connect und Hilfe zu Rate-Limits. Das Beispielprojekt fehlt im Build. Für JOSS braucht es CITATION.cff, CI und ein englisches README.
45. **Pseudonymisierung vor dem Senden** · M · ✓ erledigt (Version 2) · `core/pseudonyms.py`, `core/prompt.py`, `gui/pseudonym_dialog.py`
    Vornamen aus den Transkripten gehen unverändert an die API. Du hast das für Version 2 vorgesehen.
46. **Selbst trainierte BERT-Modelle als Kodierer** · S (Import) bis M (Server) · offen, vom User zurückgestellt (2026-10-12) · neu: Import von Vorhersagen, später Anbietertyp „Klassifikator“
    Erster Schritt: Vorhersagen (ID, Code, Wahrscheinlichkeit) importieren und wie ein Panel-Modell auswerten, mit Liste der Trainings-IDs, die wie Beispiele aus allen Auswertungen fallen. Später optional ein Anbietertyp, der einen lokalen Klassifikations-Server per HTTP fragt; torch nicht in die Exe. Zu beachten: deterministisch (ein Durchlauf, Wahrscheinlichkeit statt Konsistenz), keine Begründungen und Signalwörter, etwa 512 Token (nicht für „Gruppe holistisch“), Freeze über die Prüfsumme der Modelldatei, Labels gleich den Code-Kürzeln.

---

## 4. Offene Entscheidungen für dich

Stand 2026-10-07: Lizenz AGPL-3.0 oder später; Rohtexte nicht standardmäßig im Replikationspaket, per Opt-in möglich; Evidenz-Repair bei kleinen Modellen aus (einschaltbar, mit Hinweis). Name und Veröffentlichungsweg entscheidet der User am Ende. Der QualCoder-Import des REFI-QDA-Exports ist vom User getestet und funktioniert.
- ~~Lizenz~~: AGPL-3.0 oder später (2026-10-07). LICENSE, Metadaten und Lizenztexte für die Exe (Qt/PySide unter LGPL samt GPL) liegen bei.
- **Name:** vor dem ersten Release festlegen, weil er in Schlüsselbund, Einstellungen, CITATION.cff und Zenodo steht.
- **Veröffentlichung des Repos:** frisches öffentliches Repo oder gefilterte Historie? Interne Notizen stecken in mehreren Commits der Historie. Dazu kommen Branch und Remote.
- ~~Gold aus dem Instrument-Hash (Punkt 6)~~: erledigt ohne Entscheidung. Neue Importe rechnen die Einheiten-Prüfsumme ohne Gold; bestehende Projekte behalten ihre, ihre Läufe und Freezes bleiben gültig.
- ~~Clustern im isolierten Modus (Punkt 15)~~: Default „gruppenweise, sobald eine Gruppen-Spalte gesetzt ist“, mit Schalter „Einheiten einer Gruppe als unabhängig behandeln“ im Split-Tab. Vom User am 2026-10-07 bestätigt.
- ~~Zweites Codesystem (Punkt 20)~~: entschieden 2026-10-07, kommt in Version 2. Umgesetzt mit eigenem Aufruf je Codesystem und Review je Codesystem (User, 2026-10-07).
- ~~Englische Ausgaben (Punkt 35)~~: entschieden 2026-10-07, vor der ersten Veröffentlichung.
- ~~Replikationspaket, Rohtexte~~: standardmäßig nicht im Paket, per Opt-in (2026-10-07; so umgesetzt). ~~IDs pseudonymisieren~~: entschieden 2026-10-07 als Opt-in (E01, G01 …); der Rerun rechnet die Zuordnung aus den Originaldaten nach.
- ~~Evidenz-Repair bei kleinen Modellen~~: aus, sobald ein Modell unter 10 Mrd. Parametern ins Panel kommt; einschaltbar mit Hinweis (2026-10-07).

---

## 5. Vereinbarter Arbeitsplan (nach dem Compact)

Vorschlag vom 2026-10-06; der User will nach dem Compact damit loslegen. Jeder Schritt mit Regressionstest, ein Commit je Baustein.

1. **Kleine Fehler, falsche Zahlen:** 2 (alte Evaluation nach Projektwechsel), 3 (Sensitivität ohne Hauptlauf als Ergebnis, Rückfall in `store.latest_run`), 4 (unvollständiges Panel still „ok“), 8 (XLSX quadratisch), 9 (Retry-After ohne Deckel, Anhalten hängt).
2. **Vorarbeit:** 7 (feste Golden-Tests für Fingerabdruck, config_hash, gerenderten Prompt).
3. **Gold und Lauf-Grundlage:** 5 (Gold-Zuordnung in den Daten-Tab, Split warnt), 6 (Gold aus dem Instrument-Hash), 1 (Report und GUIDE aus dem Lauf statt aus der aktuellen Konfiguration).
4. **Code-Gruppen:** 10 (Oberkategorien, Evaluation auf beiden Ebenen; `parent` beim Speichern erhalten).
5. Danach Teil 2 nach Rücksprache.

**Stand 2026-10-06 abends:** Schritte 1–4 erledigt (Punkte 1–10), je mit Regressionstest; danach ein unabhängiger Review, 7 Befunde behoben (`f97c923`). 294 Tests grün. Offen ist Teil 2 nach Rücksprache.

**Vorgeschlagene Defaults** für die zwei Entscheidungen, die dabei anfallen (vom User noch nicht bestätigt):
- Punkt 6: Die Hashes bestehender Projekte dürfen sich einmalig ändern (betrifft nur Abnahmeprojekte).
- Punkt 15: Cluster-Bootstrap und gruppierter Split immer, sobald eine Gruppen-Spalte gesetzt ist.
