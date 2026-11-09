# Prüfliste: Exe auf einem fremden Rechner

Abnahmekriterium P5 (PLAN Abschnitt 10): Installation auf einem fremden Windows-Rechner ohne
Python. Vor jedem Release einmal durchgehen.

**Vorab, auf dem Build-Rechner:** `uv run pyinstaller packaging/llmanno.spec --noconfirm`, dann
`dist/llmanno` als ZIP packen. Lokal ohne Python im PATH geprüft (2026-10-07, zuletzt 2026-10-08 mit Benutzerführung und Hilfe): Oberfläche startet,
„Demo öffnen“ lädt die Demo, `llmanno-rerun.exe --help` läuft, der Rerun eines Pakets kommt bis
zum Modellaufruf; kein Eintrag im Fehlerprotokoll.

**Auf dem fremden Rechner** (Windows 10 oder 11, kein Python, kein uv):

1. ZIP in einen Pfad mit Leerzeichen und Umlaut entpacken, etwa `C:\Users\<Name>\Prüf Ordner\llmanno`.
2. `llmanno.exe` starten. SmartScreen fragt, weil die Exe nicht signiert ist („Weitere
   Informationen“ → „Trotzdem ausführen“).
3. **Demo öffnen**, dann die Tabs Ergebnisse, Review, Evaluation (berechnen, Kurzurteil oben) und Split ansehen. **F1** öffnet die Anleitung zum Tab; die Leiste „Nächster Schritt“ führt über die Tabs.
   Danach **Datei → Neues Projekt…**: Der Assistent führt durch Ordner, Daten, Codebuch und Modell (mit dem Beispiel-Ordner neben der Exe).
4. Export der Demo: XLSX, REFI-QDA (.qdpx), Methoden-Report als DOCX und HTML,
   Replikationspaket. Jede Datei öffnet sich im passenden Programm.
5. Einstellungen: Oberfläche auf Englisch umstellen, neu starten, **F1** zeigt die englische Anleitung; zurück auf Deutsch.
6. Tab Modelle: Anbieter mit API-Key anlegen (KI:connect oder GWDG). Der Key landet in der
   Windows-Anmeldeinformationsverwaltung; Verbindungstest; ein Modell ins Panel.
7. Eigenes kleines Projekt (Beispiel-Ordner neben der Exe): Daten importieren, Codebuch laden,
   Pilotlauf mit wenigen Einheiten.
8. `llmanno-rerun.exe` in einer Konsole auf das Replikationspaket aus Schritt 7 anwenden
   (`--out` mit leerem Ordner, `--consent` nur bei synthetischen Daten).
9. Optional: den Ordner nach `C:\Program Files\llmanno` kopieren und von dort starten
   (keine Schreibrechte im Programmordner).
10. Am Ende: `%LOCALAPPDATA%\llmanno\LLM-Annotation\fehler.log` sollte nicht existieren. Wenn doch,
   Datei mitbringen.

Notieren: Windows-Version, ob ein Virenscanner angeschlagen hat, Startzeit beim ersten Start.
