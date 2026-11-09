"""Ausgabesprache von Report, Präregistrierung, GUIDE-LLM und Kernmeldungen (Roadmap 35).

Die Exporte nehmen `lang` an und setzen sie für die Dauer des Renderns (`output_language`);
Texte stehen paarweise im Code, `t("deutsch", "english")`, damit beide Fassungen nebeneinander
gepflegt werden. Meldungen, die mit einer Auswertung entstehen und später in beiden Sprachen
gebraucht werden können (Warnungen), tragen beide Fassungen (`Message`).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

LANGUAGES = ("de", "en")
_LANG: ContextVar[str] = ContextVar("llmanno_output_language", default="de")


@contextmanager
def output_language(lang: str) -> Iterator[None]:
    if lang not in LANGUAGES:
        raise ValueError(f"Unbekannte Ausgabesprache: {lang}")
    token = _LANG.set(lang)
    try:
        yield
    finally:
        _LANG.reset(token)


def current() -> str:
    return _LANG.get()


def t(de: str, en: str) -> str:
    """Der Text in der aktuellen Ausgabesprache."""
    return en if _LANG.get() == "en" else de


class Message(str):
    """Meldung in beiden Sprachen. Als str ist sie die deutsche Fassung (Protokoll, Tests)."""

    en: str

    def __new__(cls, de: str, en: str) -> Message:
        message = super().__new__(cls, de)
        message.en = en
        return message

    def __reduce__(self):  # pickle und copy behalten beide Fassungen
        return (Message, (str(self), self.en))


def localized(text: str, lang: str | None = None) -> str:
    """Eine Meldung in der gewünschten (sonst der aktuellen) Sprache; andere Texte unverändert."""
    if (lang or _LANG.get()) == "en" and isinstance(text, Message):
        return text.en
    return str(text)


def number(value: float | None, digits: int = 2) -> str:
    """Zahl mit Dezimalkomma (Deutsch) oder -punkt (Englisch); fehlende Werte als „–“."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if _LANG.get() == "de" else text
