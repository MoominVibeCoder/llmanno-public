"""Erzeugt das Beispielprojekt: synthetische Mathe-Unterrichtsgespräche mit zwei Gold-Kodierungen.

Deterministisch (fester Seed), damit Tests und Doku dieselben Daten sehen:

    uv run python examples/make_example.py
"""

from __future__ import annotations

import random
from pathlib import Path

import openpyxl

# Im Paket, damit „Demo öffnen“ und die Exe die Daten finden.
OUT = Path(__file__).resolve().parent.parent / "src" / "llmanno" / "demo"
SEED = 7
N_TRANSCRIPTS = 10
TURNS = 20

# (Nominativ, Akkusativ, Definition)
TOPICS = [
    ("ein Bruch", "einen Bruch", "eine Zahl, die einen Teil eines Ganzen beschreibt"),
    ("der Nenner", "den Nenner", "die Zahl unter dem Bruchstrich"),
    ("der Zähler", "den Zähler", "die Zahl über dem Bruchstrich"),
    ("ein rechter Winkel", "einen rechten Winkel", "ein Winkel mit genau neunzig Grad"),
    ("der Flächeninhalt", "den Flächeninhalt", "die Größe einer Fläche in Quadrateinheiten"),
    ("eine Primzahl", "eine Primzahl", "eine Zahl, die nur durch eins und sich selbst teilbar ist"),
    ("der Umfang", "den Umfang", "die Länge des Randes einer Figur"),
    (
        "eine Gleichung",
        "eine Gleichung",
        "zwei Terme, die durch ein Gleichheitszeichen verbunden sind",
    ),
]

TEMPLATES = {
    "K1": [
        "Was ist {topic}?",
        "Wer kann erklären, was {topic} ist?",
        "Wie würdet ihr {topic_acc} jemandem beschreiben?",
        "Was wisst ihr schon über {topic_acc}?",
        "Warum brauchen wir {topic_acc} hier?",
    ],
    "K2": [
        "{Topic} ist {definition}.",
        "Ich glaube, {topic} ist {definition}.",
        "Ist {topic} nicht {definition}?",
        "Das ist doch {definition}, oder?",
        "Weil {topic} {definition} ist.",
    ],
    "K3": [
        "Genau, das stimmt.",
        "Sehr gut erklärt.",
        "Fast, denk noch mal über {topic_acc} nach.",
        "Nein, das passt so noch nicht.",
        "Richtig, und das ist wichtig für die nächste Aufgabe.",
    ],
    "K4": [
        "Schlagt bitte Seite {page} auf.",
        "Ihr habt noch fünf Minuten.",
        "Holt bitte eure Hefte raus.",
        "Wir machen jetzt in Gruppen weiter.",
        "Schreibt euch das bitte ab.",
    ],
    "KEIN_CODE": ["Äh …", "Mhm.", "(unverständlich)", "Hä?"],
}

# Plausible Verwechslungen für die zweite Kodierung: nah beieinanderliegende Codes.
CONFUSIONS = {"K1": "K4", "K2": "K1", "K3": "K2", "K4": "K1", "KEIN_CODE": "K2"}

CODEBOOK = """# Codebuch: Gesprächszüge im Mathematikunterricht

Analyseeinheit ist ein einzelner Gesprächszug (Turn). Kodiert wird die Funktion des Zuges im
Unterrichtsgespräch, nicht seine fachliche Richtigkeit. Jeder Zug erhält genau einen Code.

### K1 — Frage der Lehrkraft
Definition: Die Lehrkraft fordert Schüler:innen mit einer inhaltlichen Frage oder einem
Denkimpuls zu einem fachlichen Beitrag auf.
Kodierregel: Nur Züge der Lehrkraft. Organisatorische Fragen fallen unter K4.
Ankerbeispiel: „Wer kann erklären, was ein Bruch ist?“
Abgrenzung: Rückfragen von Schüler:innen sind K2.

### K2 — Schülerbeitrag
Definition: Eine Schülerin oder ein Schüler äußert sich inhaltlich, als Antwort, Vermutung,
Begründung oder fachliche Rückfrage.
Kodierregel: Auch unsichere oder falsche Beiträge sind K2.
Ankerbeispiel: „Ich glaube, der Nenner ist die Zahl unter dem Bruchstrich.“
Abgrenzung: Füllwörter ohne Inhalt sind KEIN_CODE.

### K3 — Rückmeldung der Lehrkraft
Definition: Die Lehrkraft bewertet, bestätigt oder korrigiert einen vorangegangenen Beitrag.
Kodierregel: Auch knappe Bestätigungen wie „Genau.“ sind K3.
Ankerbeispiel: „Fast, denk noch mal über den Zähler nach.“
Abgrenzung: Folgt auf die Rückmeldung eine neue Frage im selben Zug, entscheidet der
überwiegende Teil des Zuges.

### K4 — Organisation
Definition: Die Lehrkraft steuert Ablauf, Material oder Sozialform, ohne inhaltlichen Impuls.
Ankerbeispiel: „Schlagt bitte Seite 42 auf.“
Abgrenzung: Inhaltliche Arbeitsaufträge mit Denkimpuls sind K1.
"""


def turn_sequence(rng: random.Random) -> list[tuple[str, str]]:
    """Abfolge (Sprecher, Code) eines Transkripts: Frage, Antwort, Rückmeldung, ab und zu
    Organisation oder ein inhaltsleerer Zug."""
    sequence: list[tuple[str, str]] = [("L", "K4")]
    while len(sequence) < TURNS:
        sequence += [("L", "K1"), ("S", "K2")]
        if rng.random() < 0.3:
            sequence.append(("S", "KEIN_CODE"))
        sequence.append(("L", "K3"))
        if rng.random() < 0.25:
            sequence.append(("L", "K4"))
    return sequence[:TURNS]


def make_rows(rng: random.Random) -> list[list[object]]:
    rows: list[list[object]] = []
    for t in range(1, N_TRANSCRIPTS + 1):
        topic, topic_acc, definition = rng.choice(TOPICS)
        for number, (speaker, code) in enumerate(turn_sequence(rng), start=1):
            if code == "K1" and rng.random() < 0.4:
                topic, topic_acc, definition = rng.choice(TOPICS)  # neues Thema mit neuer Frage
            text = rng.choice(TEMPLATES[code]).format(
                topic=topic, topic_acc=topic_acc, Topic=topic[0].upper() + topic[1:],
                definition=definition,
                page=rng.randint(10, 120),
            )  # fmt: skip
            coder_b = CONFUSIONS[code] if rng.random() < 0.12 else code
            unit_id = f"T{t:02d}-{number:02d}"
            rows.append([unit_id, f"T{t:02d}", number, speaker, text, code, coder_b])
    return rows


def main() -> None:
    OUT.mkdir(exist_ok=True)
    rows = make_rows(random.Random(SEED))
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Turns"
    sheet.append(["ID", "Transkript", "Nr", "Sprecher", "Text", "Coder A", "Coder B"])
    for row in rows:
        sheet.append(row)
    workbook.save(OUT / "daten.xlsx")
    (OUT / "codebuch.md").write_text(CODEBOOK, encoding="utf-8")
    print(f"{len(rows)} Einheiten nach {OUT}")


if __name__ == "__main__":
    main()
