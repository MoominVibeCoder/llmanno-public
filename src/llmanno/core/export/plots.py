"""SVG-Grafiken für Report und Evaluation: Reliability, Risk-Coverage, Prävalenz.

Ohne Plot-Bibliothek: Der HTML-Report bettet die SVGs ein, die Oberfläche rendert sie mit QtSvg.
Beschriftungen sind Parameter, damit die Oberfläche übersetzte Texte einsetzen kann.
"""

from __future__ import annotations

import math
from html import escape

from llmanno.core.evaluation import PrevalenceRow
from llmanno.core.metrics.calibration import CalibrationReport

SIZE, PAD = 220, 30
BLUE, RED, GREY, ORANGE = "#256b86", "#b3261e", "#6b7280", "#b45309"
FRAME = "fill='#f9fafb' stroke='#d1d5db'"


def _n(value: float, digits: int, decimal: str) -> str:
    return "–" if math.isnan(value) else f"{value:.{digits}f}".replace(".", decimal)


def _square(name: str, label: str, body: str, x_label: str, y_label: str) -> str:
    plot = SIZE - 2 * PAD
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{SIZE}' height='{SIZE}' "
        f"viewBox='0 0 {SIZE} {SIZE}' role='img' aria-label='{escape(label)} {escape(name)}' "
        "style='margin-right:1rem'>"
        f"<rect x='{PAD}' y='{PAD}' width='{plot}' height='{plot}' {FRAME}/>{body}"
        f"<text x='{SIZE / 2}' y='{PAD - 10}' text-anchor='middle' font-size='11' "
        f"font-weight='bold'>{escape(name)}</text>"
        f"<text x='{SIZE / 2}' y='{SIZE - 8}' text-anchor='middle' font-size='11'>"
        f"{escape(x_label)}</text>"
        f"<text x='12' y='{SIZE / 2}' font-size='11' transform='rotate(-90 12 {SIZE / 2})' "
        f"text-anchor='middle'>{escape(y_label)}</text></svg>"
    )


def reliability_svg(
    name: str,
    calibration: CalibrationReport,
    x_label: str = "Konfidenz",
    y_label: str = "Trefferquote",
    decimal: str = ",",
) -> str:
    """Trefferquote je Konfidenzklasse gegen die Diagonale (perfekt kalibriert); Balkenbreite =
    Klasse, Deckkraft wächst mit der Fallzahl."""
    plot = SIZE - 2 * PAD
    peak = max((b.n for b in calibration.bins), default=0) or 1

    def x(v: float) -> float:
        return PAD + v * plot

    def y(v: float) -> float:
        return SIZE - PAD - v * plot

    bars = "".join(
        f"<rect x='{x(b.low):.1f}' y='{y(b.accuracy):.1f}' width='{(b.high - b.low) * plot:.1f}' "
        f"height='{b.accuracy * plot:.1f}' fill='{BLUE}' "
        f"fill-opacity='{0.25 + 0.75 * b.n / peak:.2f}'>"
        f"<title>{_n(b.low, 1, decimal)}–{_n(b.high, 1, decimal)}: {escape(y_label)} "
        f"{_n(b.accuracy, 2, decimal)} (n {b.n})</title></rect>"
        for b in calibration.bins
        if b.n
    )
    diagonal = (
        f"<line x1='{x(0)}' y1='{y(0)}' x2='{x(1)}' y2='{y(1)}' stroke='{RED}' "
        "stroke-dasharray='4 3'/>"
    )
    return _square(name, "Reliability-Diagramm", bars + diagonal, x_label, y_label)


def risk_coverage_svg(
    name: str,
    calibration: CalibrationReport,
    x_label: str = "Abdeckung",
    y_label: str = "Fehlerrate",
    decimal: str = ",",
) -> str:
    """Fehlerrate der angenommenen Einheiten, wenn man nach absteigender Konfidenz annimmt.
    Gestrichelt die Fehlerrate ohne Auswahl; je tiefer die Kurve links, desto nützlicher die
    Konfidenz als Filter."""
    plot = SIZE - 2 * PAD
    curve = calibration.curve
    top = max(0.1, math.ceil(max((r for _, r in curve), default=0) * 10) / 10)

    def x(v: float) -> float:
        return PAD + v * plot

    def y(v: float) -> float:
        return SIZE - PAD - v / top * plot

    points = " ".join(f"{x(c):.1f},{y(r):.1f}" for c, r in curve)
    body = f"<polyline points='{points}' fill='none' stroke='{BLUE}' stroke-width='2'/>"
    if curve:
        base = curve[-1][1]
        body += (
            f"<line x1='{x(0)}' y1='{y(base):.1f}' x2='{x(1)}' y2='{y(base):.1f}' "
            f"stroke='{RED}' stroke-dasharray='4 3'/>"
        )
    body += (
        f"<text x='{PAD - 3}' y='{PAD + 4}' text-anchor='end' font-size='9'>"
        f"{_n(top, 1, decimal)}</text>"
        f"<text x='{PAD - 3}' y='{SIZE - PAD}' text-anchor='end' font-size='9'>0</text>"
    )
    return _square(name, "Risk-Coverage-Kurve", body, x_label, y_label)


def prevalence_svg(
    rows: list[PrevalenceRow],
    labels: tuple[str, str, str] = ("LLM naiv", "nur Stichprobe", "PPI++"),
    decimal: str = ",",
) -> str:
    """Anteil je Code: naiv aus den LLM-Labels (Punkt), aus der Stichprobe und per PPI++ (je mit
    95-%-KI). Schmalere PPI++-Balken heißen: Die LLM-Labels sparen Review-Aufwand."""
    left, right, line, top = 70, 20, 12, 34
    width = 420
    plot = width - left - right
    height = top + len(rows) * 3 * line + 26
    high = max([0.1, *(max(r.llm, r.sample.high, r.ppi.high) for r in rows)])
    scale = min(1.0, math.ceil(high * 10) / 10)

    def x(v: float) -> float:
        return left + max(0.0, min(v, scale)) / scale * plot

    colors = (GREY, ORANGE, BLUE)
    legend = "".join(
        f"<circle cx='{left + i * 110}' cy='12' r='4' fill='{color}'/>"
        f"<text x='{left + 8 + i * 110}' y='16' font-size='11'>{escape(label)}</text>"
        for i, (label, color) in enumerate(zip(labels, colors, strict=True))
    )
    body = []
    for index, r in enumerate(rows):
        base = top + index * 3 * line
        body.append(
            f"<text x='{left - 6}' y='{base + 1.5 * line + 4}' text-anchor='end' "
            f"font-size='11'>{escape(r.code)}</text>"
        )
        for j, (est, color) in enumerate(zip((None, r.sample, r.ppi), colors, strict=True)):
            cy = base + (j + 0.5) * line
            value = r.llm if est is None else est.value
            if est is not None and not math.isnan(est.low):
                body.append(
                    f"<line x1='{x(est.low):.1f}' y1='{cy}' x2='{x(est.high):.1f}' y2='{cy}' "
                    f"stroke='{color}' stroke-width='2'/>"
                )
            if not math.isnan(value):
                body.append(f"<circle cx='{x(value):.1f}' cy='{cy}' r='3.5' fill='{color}'/>")
    bottom = top + len(rows) * 3 * line
    axis = (
        f"<rect x='{left}' y='{top - 4}' width='{plot}' height='{bottom - top + 8}' {FRAME}/>"
        f"<text x='{left}' y='{bottom + 18}' font-size='9'>0</text>"
        f"<text x='{left + plot}' y='{bottom + 18}' text-anchor='end' font-size='9'>"
        f"{_n(scale, 1, decimal)}</text>"
    )
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        f"viewBox='0 0 {width} {height}' role='img' aria-label='Prävalenz je Code'>"
        f"{legend}{axis}{''.join(body)}</svg>"
    )
